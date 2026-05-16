import os
import time
import wave
import signal
import threading
import subprocess
from datetime import datetime


class AudioController:
    """
    树莓派 USB 音频模块驱动接口。

    默认音频设备：
        plughw:2,0

    功能：
        1. play_wav()             播放 wav 文件
        2. record_seconds()       固定时长录音，时间和保存路径由 App 传入
        3. start_recording()      开始录音，保存路径由 App 传入
        4. stop_recording()       停止录音
        5. start_stream()         实时采集麦克风 PCM 数据
        6. stop_stream()          停止实时采集
    """

    def __init__(
        self,
        audio_device="plughw:CARD=Device,DEV=0",
        default_record_dir="/home/pi/car_project/records",
        sample_rate=16000,
        channels=1,
    ):
        self.audio_device = audio_device
        self.default_record_dir = default_record_dir
        self.sample_rate = sample_rate
        self.channels = channels

        os.makedirs(self.default_record_dir, exist_ok=True)

        self.play_process = None
        self.record_process = None
        self.stream_process = None

        self.play_lock = threading.Lock()
        self.record_lock = threading.Lock()
        self.stream_lock = threading.Lock()

        self.stream_thread = None
        self.stream_running = False
        self.current_record_path = None

    # =========================
    # 播放音频
    # =========================

    def play_wav(self, wav_path, interrupt=True):
        """
        播放 wav 文件。

        wav_path:
            App 层传入完整音频路径。

        interrupt=True:
            如果上一段声音还没播完，先停止上一段，再播放新的。
        """
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"音频文件不存在: {wav_path}")

        with self.play_lock:
            if interrupt:
                self._stop_playback_locked()
            else:
                if self.play_process is not None and self.play_process.poll() is None:
                    print("audio busy, skip:", wav_path)
                    return

            self.play_process = subprocess.Popen([
                "aplay",
                "-D",
                self.audio_device,
                wav_path
            ])

    def stop_playback(self):
        with self.play_lock:
            self._stop_playback_locked()

    def _stop_playback_locked(self):
        if self.play_process is not None and self.play_process.poll() is None:
            self.play_process.terminate()
            try:
                self.play_process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                self.play_process.kill()

        self.play_process = None

    # =========================
    # 固定时长录音
    # =========================

    def record_seconds(self, duration, save_path, sample_rate=None):
        """
        固定录音 duration 秒，保存到 App 指定路径。

        参数：
            duration:
                录音秒数，由 App 层传入。
                例如 3、5、10。

            save_path:
                保存的完整 wav 路径，由 App 层传入。
                例如：
                /home/pi/car_project/records/test.wav

            sample_rate:
                采样率，不传默认 16000Hz。

        返回：
            实际保存的 wav 文件路径。
        """
        if duration <= 0:
            raise ValueError("duration 必须大于 0")

        path = self._normalize_wav_path(save_path)
        rate = sample_rate or self.sample_rate

        os.makedirs(os.path.dirname(path), exist_ok=True)

        subprocess.run([
            "arecord",
            "-D",
            self.audio_device,
            "-f",
            "S16_LE",
            "-r",
            str(rate),
            "-c",
            str(self.channels),
            "-d",
            str(duration),
            path
        ], check=True)
        time.sleep(0.3)

        return path

    # =========================
    # 按住式录音
    # =========================

    def start_recording(self, save_path, sample_rate=None):
        """
        开始录音，不阻塞。

        参数：
            save_path:
                App 层指定保存路径。
                例如：
                /home/pi/car_project/records/press_record.wav

        使用方式：
            App 按下录音按钮 -> start_recording(save_path)
            App 松开录音按钮 -> stop_recording()
        """
        with self.record_lock:
            if self.record_process is not None and self.record_process.poll() is None:
                raise RuntimeError("当前已经在录音，请先 stop_recording()")

            path = self._normalize_wav_path(save_path)
            rate = sample_rate or self.sample_rate

            os.makedirs(os.path.dirname(path), exist_ok=True)

            self.record_process = subprocess.Popen([
                "arecord",
                "-D",
                self.audio_device,
                "-f",
                "S16_LE",
                "-r",
                str(rate),
                "-c",
                str(self.channels),
                path
            ])

            self.current_record_path = path
            return path

    def stop_recording(self):
        """
        停止当前录音。

        返回：
            保存的 wav 文件路径。
        """
        with self.record_lock:
            if self.record_process is None:
                return None

            if self.record_process.poll() is None:
                # 用 SIGINT 停止，让 arecord 正常写入 wav 文件头
                self.record_process.send_signal(signal.SIGINT)
                try:
                    self.record_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.record_process.terminate()
                    self.record_process.wait(timeout=1)

            path = self.current_record_path

            self.record_process = None
            self.current_record_path = None
            time.sleep(0.3)
            return path

    def is_recording(self):
        return self.record_process is not None and self.record_process.poll() is None

    # =========================
    # 实时麦克风流
    # =========================

    def start_stream(self, on_audio_chunk, chunk_duration=0.1, sample_rate=None):
        """
        开始实时采集麦克风数据。

        on_audio_chunk:
            App 层传入回调函数。
            每收到一段 PCM 音频数据，就调用：
                on_audio_chunk(data)

        chunk_duration:
            每次回调的数据时长，默认 0.1 秒。
        """
        with self.stream_lock:
            if self.stream_running:
                raise RuntimeError("实时音频流已经在运行")

            rate = sample_rate or self.sample_rate
            bytes_per_sample = 2
            chunk_size = int(rate * self.channels * bytes_per_sample * chunk_duration)

            self.stream_running = True

            self.stream_process = subprocess.Popen([
                "arecord",
                "-D",
                self.audio_device,
                "-f",
                "S16_LE",
                "-r",
                str(rate),
                "-c",
                str(self.channels),
                "-t",
                "raw"
            ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            def stream_loop():
                while self.stream_running:
                    try:
                        data = self.stream_process.stdout.read(chunk_size)

                        if not data:
                            time.sleep(0.01)
                            continue

                        on_audio_chunk(data)

                    except Exception as e:
                        if self.stream_running:
                            print("audio stream error:", e)
                        time.sleep(0.1)

            self.stream_thread = threading.Thread(
                target=stream_loop,
                daemon=True
            )
            self.stream_thread.start()

    def stop_stream(self):
        with self.stream_lock:
            self.stream_running = False

            if self.stream_process is not None:
                if self.stream_process.poll() is None:
                    self.stream_process.terminate()
                    try:
                        self.stream_process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.stream_process.kill()

            self.stream_process = None
            self.stream_thread = None

    # =========================
    # 资源释放
    # =========================

    def close(self):
        try:
            self.stop_stream()
        except Exception:
            pass

        try:
            self.stop_recording()
        except Exception:
            pass

        try:
            self.stop_playback()
        except Exception:
            pass

    # =========================
    # 内部工具函数
    # =========================

    def _normalize_wav_path(self, path):
        """
        统一处理 App 传进来的保存路径。
        如果没写 .wav，自动补 .wav。
        如果只传文件名，就保存到默认目录。
        """
        if not path.endswith(".wav"):
            path += ".wav"

        if os.path.isabs(path):
            return path

        return os.path.join(self.default_record_dir, path)


if __name__ == "__main__":
    audio = AudioController()

    print("测试：录音 3 秒")
    path = audio.record_seconds(
        duration=3,
        save_path="/home/pi/car_project/records/test_record.wav"
    )

    print("录音保存到:", path)
    print("播放刚才录音")
    audio.play_wav(path)

    time.sleep(4)
    audio.close()
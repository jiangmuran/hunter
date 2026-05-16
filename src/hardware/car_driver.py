import serial
import time
import threading
import subprocess
import os


# =========================
# 音频配置
# =========================

AUDIO_DEVICE = "plughw:CARD=Device,DEV=0"

SOUND_OBSTACLE_ON = "/home/pi/car_project/sounds/obstacle_on.wav"
SOUND_OBSTACLE_OFF = "/home/pi/car_project/sounds/obstacle_off.wav"
SOUND_EMERGENCY_STOP = "/home/pi/car_project/sounds/emergency_stop.wav"

SOUND_CAT_1 = "/home/pi/car_project/sounds/cat1.wav"
SOUND_CAT_2 = "/home/pi/car_project/sounds/cat2.wav"
SOUND_CAT_3 = "/home/pi/car_project/sounds/cat3.wav"
SOUND_CAT_4 = "/home/pi/car_project/sounds/cat4.wav"


class CarController:
    """
    树莓派控制 ESP32 全向轮小车的 Python 驱动接口。

    串口：
        默认使用固定串口路径：
        /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0

    小车运动接口：
        forward()
        backward()
        left()
        right()
        rotate_cw()
        rotate_ccw()
        stop()

    ESP32 自主避障：
        obstacle_on()
        obstacle_off()

    上层 App 自定义避障：
        ultrasonic_report_on()
        ultrasonic_report_off()

    速度控制：
        speed_up()
        speed_down()
        speed_reset()

    猫叫/提示音：
        play_cat_1()
        play_cat_2()
        play_cat_3()
        play_cat_4()

    超声波数据：
        set_ultrasonic_callback(callback)
        get_latest_ultrasonic()

    手柄 L1/L2 回调：
        set_ps3_l1_callback(callback)
        set_ps3_l2_callback(callback)
    """

    def __init__(
        self,
        port="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
        baudrate=115200,
        enable_voice=True,
        ultrasonic_callback=None,
        ps3_l1_callback=None,
        ps3_l2_callback=None,
    ):
        self.port = port
        self.baudrate = baudrate
        self.enable_voice = enable_voice

        self.serial_lock = threading.Lock()
        self.audio_lock = threading.Lock()
        self.ultrasonic_lock = threading.Lock()

        self.running = True
        self.audio_process = None

        # 上层 App 可选传入超声波回调函数
        # 回调数据格式：
        # {
        #     "sensor_id": 1,
        #     "distance_mm": 180,
        #     "has_obstacle": True
        # }
        self.ultrasonic_callback = ultrasonic_callback

        # 上层 App 可选传入手柄 L1/L2 回调函数
        # 用于：手柄 L1/L2 -> ESP32 串口上报 -> 树莓派执行机械臂固定动作
        self.ps3_l1_callback = ps3_l1_callback
        self.ps3_l2_callback = ps3_l2_callback

        # 保存最新一次三个超声波的数据
        self.latest_ultrasonic = {
            1: None,
            2: None,
            3: None,
        }

        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=0.1
        )

        # ESP32 打开串口时可能会自动复位
        # ESP32 setup() 里还有 delay(2000)，这里等 4 秒更稳
        time.sleep(4)

        # 后台线程：监听 ESP32 返回状态
        self.reader_thread = threading.Thread(
            target=self._serial_reader_loop,
            daemon=True
        )
        self.reader_thread.start()

    # =========================
    # 音频播放
    # =========================

    def play_wav(self, wav_path):
        """
        播放 wav 文件。

        如果上一段声音还没播完，先停止上一段，再播放新的。
        这样可以避免 aplay 报 Device or resource busy。
        """
        if not self.enable_voice:
            return

        if not os.path.exists(wav_path):
            print("声音文件不存在:", wav_path)
            return

        with self.audio_lock:
            if self.audio_process is not None:
                if self.audio_process.poll() is None:
                    self.audio_process.terminate()
                    try:
                        self.audio_process.wait(timeout=0.3)
                    except subprocess.TimeoutExpired:
                        self.audio_process.kill()

            self.audio_process = subprocess.Popen([
                "aplay",
                "-D",
                AUDIO_DEVICE,
                wav_path
            ])

    def play_obstacle_on(self):
        self.play_wav(SOUND_OBSTACLE_ON)

    def play_obstacle_off(self):
        self.play_wav(SOUND_OBSTACLE_OFF)

    def play_emergency_stop(self):
        self.play_wav(SOUND_EMERGENCY_STOP)

    def play_cat_1(self):
        self.play_wav(SOUND_CAT_1)

    def play_cat_2(self):
        self.play_wav(SOUND_CAT_2)

    def play_cat_3(self):
        self.play_wav(SOUND_CAT_3)

    def play_cat_4(self):
        self.play_wav(SOUND_CAT_4)

    # =========================
    # 串口监听
    # =========================

    def _serial_reader_loop(self):
        """
        后台读取 ESP32 返回状态。

        只处理白名单状态：
            OBSTACLE_ON
            OBSTACLE_OFF
            EMERGENCY_STOP
            STOP
            ULTRA_REPORT_ON
            ULTRA_REPORT_OFF
            AUTO_OBSTACLE_BLOCKED_ULTRA_REPORT_ON
            ULTRA,1,180,1
            BUTTON_SPEED,2000
            PS3_L1_DOWN
            PS3_L2_DOWN

        其他无关日志全部忽略，避免 PS3_L2CAP 等日志干扰上层。
        """
        while self.running:
            try:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode(
                        "utf-8",
                        errors="ignore"
                    ).strip()

                    if not line:
                        continue

                    self._handle_esp32_line(line)

                time.sleep(0.02)

            except Exception as e:
                if self.running:
                    print("serial reader error:", e)
                time.sleep(0.2)

    def _handle_esp32_line(self, line):
        """
        处理 ESP32 串口返回的一行数据。
        """
        if line == "OBSTACLE_ON":
            print("esp32:", line)
            self.play_obstacle_on()

        elif line == "OBSTACLE_OFF":
            print("esp32:", line)
            self.play_obstacle_off()

        elif line == "EMERGENCY_STOP":
            print("esp32:", line)
            self.play_emergency_stop()

        elif line == "STOP":
            print("esp32:", line)

        elif line == "ULTRA_REPORT_ON":
            print("esp32:", line)

        elif line == "ULTRA_REPORT_OFF":
            print("esp32:", line)

        elif line == "AUTO_OBSTACLE_BLOCKED_ULTRA_REPORT_ON":
            print("esp32:", line)

        elif line == "3WD-ROBOT-START":
            print("esp32:", line)

        elif line.startswith("BUTTON_SPEED,"):
            # 例如：BUTTON_SPEED,2200
            print("esp32:", line)

        elif line == "PS3_L1_DOWN":
            print("esp32:", line)
            self._run_callback_async(
                self.ps3_l1_callback,
                "PS3_L1_DOWN"
            )

        elif line == "PS3_L2_DOWN":
            print("esp32:", line)
            self._run_callback_async(
                self.ps3_l2_callback,
                "PS3_L2_DOWN"
            )

        elif line.startswith("ULTRA,"):
            data = self.parse_ultrasonic_line(line)

            if data is not None:
                print("esp32:", line)

                sensor_id = data["sensor_id"]

                with self.ultrasonic_lock:
                    self.latest_ultrasonic[sensor_id] = data

                if self.ultrasonic_callback is not None:
                    try:
                        self.ultrasonic_callback(data)
                    except Exception as e:
                        print("ultrasonic callback error:", e)

        else:
            # 其他日志全部忽略，比如 PS3_L2CAP 报错
            pass

    def _run_callback_async(self, callback, name):
        """
        后台执行回调，避免机械臂动作阻塞串口监听线程。
        """
        if callback is None:
            print(f"{name} callback is not set")
            return

        def worker():
            try:
                callback()
            except Exception as e:
                print(f"{name} callback error:", e)

        threading.Thread(target=worker, daemon=True).start()

    def parse_ultrasonic_line(self, line):
        """
        解析 ESP32 上报的超声波数据。

        输入格式：
            ULTRA,1,180,1

        返回：
            {
                "sensor_id": 1,
                "distance_mm": 180,
                "has_obstacle": True
            }
        """
        parts = line.split(",")

        if len(parts) != 4:
            return None

        if parts[0] != "ULTRA":
            return None

        try:
            sensor_id = int(parts[1])
            distance_mm = int(parts[2])
            has_obstacle = bool(int(parts[3]))

            if sensor_id not in (1, 2, 3):
                return None

            return {
                "sensor_id": sensor_id,
                "distance_mm": distance_mm,
                "has_obstacle": has_obstacle,
            }

        except ValueError:
            return None

    def set_ultrasonic_callback(self, callback):
        """
        设置超声波数据回调函数。

        App 层可以用这个接口接收实时 ULTRA 数据。
        """
        self.ultrasonic_callback = callback

    def get_latest_ultrasonic(self):
        """
        获取最近一次三个超声波的数据。

        返回示例：
        {
            1: {"sensor_id": 1, "distance_mm": 180, "has_obstacle": True},
            2: {"sensor_id": 2, "distance_mm": 350, "has_obstacle": False},
            3: {"sensor_id": 3, "distance_mm": 120, "has_obstacle": True},
        }
        """
        with self.ultrasonic_lock:
            return dict(self.latest_ultrasonic)

    def set_ps3_l1_callback(self, callback):
        """
        设置手柄 L1 按下后的回调函数。

        使用场景：
            手柄 L1 -> ESP32 上报 PS3_L1_DOWN -> 树莓派执行机械臂固定动作 1
        """
        self.ps3_l1_callback = callback

    def set_ps3_l2_callback(self, callback):
        """
        设置手柄 L2 按下后的回调函数。

        使用场景：
            手柄 L2 -> ESP32 上报 PS3_L2_DOWN -> 树莓派执行机械臂固定动作 2
        """
        self.ps3_l2_callback = callback

    # =========================
    # 串口发送
    # =========================

    def send(self, cmd):
        """
        发送单字符命令给 ESP32。
        """
        if not isinstance(cmd, str):
            raise TypeError("cmd 必须是字符串")

        if len(cmd) != 1:
            raise ValueError("cmd 必须是单个字符，例如 'w' 或 's'")

        with self.serial_lock:
            self.ser.write(cmd.encode("ascii"))
            self.ser.flush()
            print("send:", cmd)

    # =========================
    # 普通运动控制
    # =========================
    # 注意：
    # 这里已经做了方向反向映射。
    # 上层 App 只需要按函数名字正常调用。
    # 不要让上层直接 send("w") / send("x")。

    def forward(self):
        """前进"""
        self.send("x")

    def backward(self):
        """后退"""
        self.send("w")

    def left(self):
        """左移"""
        self.send("d")

    def right(self):
        """右移"""
        self.send("a")

    def turn_left(self):
        """左旋 / 逆时针旋转"""
        self.send("c")

    def turn_right(self):
        """右旋 / 顺时针旋转"""
        self.send("z")

    def rotate_ccw(self):
        """逆时针旋转"""
        self.send("c")

    def rotate_cw(self):
        """顺时针旋转"""
        self.send("z")

    def stop(self):
        """停止普通运动"""
        self.send("s")

    # =========================
    # 速度控制
    # =========================

    def speed_up(self):
        """
        增加小车 BUTTONSPEED。

        ESP32 侧会把 BUTTONSPEED 增加 200，最大值由 ESP32 代码限制。
        影响：
            1. 树莓派串口运动速度
            2. 手柄十字键速度
            3. 手柄方块/圆圈旋转速度
            4. ESP32 自主避障动作速度
        不影响：
            手柄摇杆速度
        """
        self.send("+")

    def speed_down(self):
        """
        降低小车 BUTTONSPEED。

        ESP32 侧会把 BUTTONSPEED 减少 200，最小值由 ESP32 代码限制。
        """
        self.send("-")

    def speed_reset(self):
        """
        恢复 BUTTONSPEED 默认值。
        """
        self.send("m")

    # =========================
    # ESP32 自主避障控制
    # =========================

    def obstacle_on(self):
        """
        开启 ESP32 自主避障。

        如果上层 App 自定义超声波上报模式已经开启，
        ESP32 会返回 AUTO_OBSTACLE_BLOCKED_ULTRA_REPORT_ON。
        """
        self.send("o")

    def obstacle_off(self):
        """
        关闭 ESP32 自主避障。
        """
        self.send("p")

    # =========================
    # 上层 App 自定义避障：超声波上报
    # =========================

    def ultrasonic_report_on(self):
        """
        开启超声波上报模式。

        ESP32 行为：
            1. 关闭 ESP32 本地自主避障
            2. 不主动控制小车避障
            3. 持续通过串口上报三个超声波数据

        上报格式：
            ULTRA,<sensor_id>,<distance_mm>,<has_obstacle>
            例如：
            ULTRA,1,180,1
        """
        self.send("u")

    def ultrasonic_report_off(self):
        """
        关闭超声波上报模式。
        """
        self.send("v")

    def emergency_stop(self):
        """
        急停。

        ESP32 会关闭所有模式，并停车。
        """
        self.send("e")

    # =========================
    # 按住式控制辅助函数
    # =========================

    def move_for(self, action, duration, interval=0.1):
        """
        持续发送某个动作一段时间，然后停止。

        action 可选：
            forward
            backward
            left
            right
            turn_left
            turn_right
            rotate_cw
            rotate_ccw
        """
        start = time.time()

        while time.time() - start < duration:
            if action == "forward":
                self.forward()
            elif action == "backward":
                self.backward()
            elif action == "left":
                self.left()
            elif action == "right":
                self.right()
            elif action == "turn_left":
                self.turn_left()
            elif action == "turn_right":
                self.turn_right()
            elif action == "rotate_cw":
                self.rotate_cw()
            elif action == "rotate_ccw":
                self.rotate_ccw()
            else:
                raise ValueError(f"未知动作: {action}")

            time.sleep(interval)

        self.stop()

    # =========================
    # 关闭资源
    # =========================

    def close(self):
        """
        关闭串口前：
            1. 停止后台监听
            2. 停止正在播放的语音
            3. 给 ESP32 发送停止命令
            4. 关闭串口
        """
        self.running = False
        time.sleep(0.1)

        with self.audio_lock:
            if self.audio_process is not None:
                if self.audio_process.poll() is None:
                    self.audio_process.terminate()
                    try:
                        self.audio_process.wait(timeout=0.3)
                    except subprocess.TimeoutExpired:
                        self.audio_process.kill()

        try:
            self.stop()
        except Exception as e:
            print("stop error:", e)

        try:
            self.ser.close()
        except Exception as e:
            print("serial close error:", e)
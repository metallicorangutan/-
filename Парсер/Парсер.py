import serial
import serial.tools.list_ports
from pathlib import Path
import time
import csv
import threading
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QVBoxLayout, QWidget, QPushButton, QLabel
import sys
from PyQt5.QtWidgets import QLineEdit, QDialog
import json
BASE = Path(__file__).parent.resolve()  # путь к папке с main.py
ICON = BASE / 'icon.png'              # иконка рядом с main.py
OUT  = BASE / 'pressure_log.csv' 
BAUD = 115200
SHOW_PLOT = True
JSON_FILE = BASE / 'config.json'
class NewPIDVID(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.label = QLabel("Введите PID и VID через точку с запятой в 16-ричном формате (например, 0xpid;0xvid):")
        new_pid_vid = QLineEdit()
        layout.addWidget(new_pid_vid)
        layout.addWidget(self.label)
        line = new_pid_vid.text()
        pid=line.split(';')[0]
        vid=line.split(';')[1]
        PID_VID={pid: vid}
        with open(JSON_FILE, 'w') as f:
            json.dump(PID_VID, f)
        self.setLayout(layout)

class LivePlot:
    def __init__(self, max_points: int = 5000, update_ms: int = 30,
                 title: str = 'Давление в реальном времени', y_label: str = 'Давление',
                 x_label: str = 'Время Ардуино, с'):
        self.max_points = max_points
        self.update_ms = update_ms
        self.title = title
        self.y_label = y_label
        self.x_label = x_label

        # кольцевой буфер
        self._buf_p = np.zeros(max_points)
        self._buf_t = np.zeros(max_points)
        self._idx = 0
        self._lock = threading.Lock()

        # Qt-объекты
        self._app = None
        self._win = None
        self._curve = None
        self._timer = None
        self._thread = None
        self._t0 = None
        self.icon_path = str(ICON)

        self._stop = threading.Event()

    # ---------- публичный API ----------
    def start(self):
        """Открыть окно в отдельном потоке (не блокирует)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # ждём, пока окно создастся
        for _ in range(50):
            if self._app is not None:
                break
            time.sleep(0.05)

    def push(self, value: float, time_ms: float):
        """Добавить точку (потокобезопасно)."""
        with self._lock:
            i = self._idx % self.max_points
            self._buf_p[i] = value
            self._buf_t[i] = time_ms
            self._idx += 1

    def stop(self):
        """Остановить и закрыть окно."""
        self._stop.set()
        if self._app is not None:
            QtCore.QMetaObject.invokeMethod(
                self._app, 'quit', QtCore.Qt.QueuedConnection)
        if self._thread:
            self._thread.join(timeout=2)

    # ---------- внутреннее ----------
    def _run(self):
        pg.setConfigOptions(antialias=True)
        self._app = pg.mkQApp(self.title)
        self._win = pg.GraphicsLayoutWidget(title=self.title)
        if self.icon_path:
            p = Path(self.icon_path)
            if p.exists():
                icon = QIcon(str(p))
                self._win.setWindowIcon(icon)
                self._app.setWindowIcon(icon) 
        self._win.showMaximized()
        plot = self._win.addPlot(title=self.title)
        plot.setLabel('left', self.y_label)
        plot.setLabel('bottom', self.x_label)
        plot.showGrid(x=True, y=True, alpha=0.3)
        self._curve = plot.plot(pen=pg.mkPen('c', width=1.5))

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(self.update_ms)
        self._app.exec_()
    def _refresh(self):
        if self._stop.is_set():
            self._app.quit()
            return
        with self._lock:
            n = min(self._idx, self.max_points)
            if n < 2:
                return
            if self._idx <= self.max_points:
                y = self._buf_p[:n].copy()
                x = self._buf_t[:n].copy()
            else:
                k = self._idx % self.max_points
                y = np.concatenate([self._buf_p[k:], self._buf_p[:k]])
                x = np.concatenate([self._buf_t[k:], self._buf_t[:k]])

        if self._t0 is None:
            self._t0 = x[0]
        xs = (x - self._t0)

        self._curve.setData(xs, y)
def int_tuple(s):
    return tuple(int(x, 16) for x in s)
class Port():
    def __init__(self):
        self.pid = None
        self.vid = None
    def find_com() ->str | None:
        with open(JSON_FILE, 'r') as f:
            pidvids = json.load(f)
        #IDs = [(0x2341,0x0043), (0x1A86, 0x7523), (0x1A86,0x5523), (0x2341, 0x0001), (0x2341, 0x0243), (0x0403, 0x6001), (0x10C4, 0xEA60)]
        ports = serial.tools.list_ports.comports()
        for p in ports:
            for i in pidvids.items():
                pidvidsint = int_tuple(i)
                if (p.vid, p.pid) == pidvidsint:
                    return str(p.device).strip()
def bootstrap():
    port = Port.find_com()
    if not port:
        raise RuntimeError('Arduino не найдена')
    print(f'Порт: {port} @ {BAUD}')
    ser = serial.Serial(port, BAUD, timeout=2)
    time.sleep(2)               
    ser.reset_input_buffer()
    time.sleep(0.5)
    return ser
def main():
    ser = bootstrap()
    f = open(OUT, 'a', newline='', encoding='utf-8')
    w = csv.writer(f)
    w.writerow(['t', 'p'])
    f.flush()
    n = 0
    plot = None
    if SHOW_PLOT:
        plot = LivePlot(
            title='Давление в реальном времени',
            y_label='Давление, бар',
            x_label='Время Arduino, с x0.01',
            max_points=5000,
            update_ms=30,
        )
        plot.start()
    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            parts = line.split(';')
            try:
                pressure = float(parts[0])
                Time = float(parts[1])/0.1
            except ValueError as e:
                print(f'Value error {e!r}| line = {line!r}')
                continue

            w.writerow([f'{Time:.6f}', f'{pressure:.6f}'])
            n += 1
            if n % 100 == 0:
                f.flush()
            if plot is not None:
                plot.push(pressure, Time)
            print(f'{pressure:.3f}, {Time:.3f}')
    except KeyboardInterrupt:
        print(f'\nЗаписано {n} строк')
    finally:
        f.flush()
        f.close()
        ser.close()
        if plot is not None:
            plot.stop()
    return 0
main()
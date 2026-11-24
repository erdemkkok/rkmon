#!/usr/bin/env python3
"""
rkmon - Orange Pi / RK3588 terminal izleme aracı
Sıcaklık, NPU, CPU (çekirdek bazlı + frekans), GPU ve RAM değerlerini
renkli çubuklar ve kayan geçmiş grafikleriyle gösterir.

Kullanım:
    sudo python3 rkmon.py              # NPU yükü için root gerekir (debugfs)
    sudo python3 rkmon.py -i 0.5       # yenileme aralığı (sn)
    sudo python3 rkmon.py --log log.csv

Tuşlar:  q çıkış | +/- aralık | c çekirdekleri göster/gizle | p duraklat
Sadece Python3 standart kütüphanesi kullanır (curses).
"""
import argparse
import curses
import glob
import locale
import os
import re
import socket
import time
from collections import deque

SPARK = " ▁▂▃▄▅▆▇█"
HIST_LEN = 300
LW = 20  # etiket sütunu genişliği

NPU_LOAD = "/sys/kernel/debug/rknpu/load"


# ---------------------------------------------------------------- okuma
def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def natural_key(p):
    m = re.findall(r"\d+", p)
    return int(m[-1]) if m else 0


class CpuSampler:
    def __init__(self):
        self.prev = self._snap()

    @staticmethod
    def _snap():
        d = {}
        for line in (read("/proc/stat") or "").splitlines():
            if not line.startswith("cpu"):
                continue
            p = line.split()
            v = list(map(int, p[1:9]))  # guest alanları hariç
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            d[p[0]] = (sum(v), idle)
        return d

    def sample(self):
        cur = self._snap()
        out = {}
        for k, (t, i) in cur.items():
            pt, pi = self.prev.get(k, (t, i))
            dt, di = t - pt, i - pi
            out[k] = 0.0 if dt <= 0 else max(0.0, min(100.0, 100.0 * (dt - di) / dt))
        self.prev = cur
        return out


def cpu_freq_mhz(n):
    v = read(f"/sys/devices/system/cpu/cpu{n}/cpufreq/scaling_cur_freq")
    return int(v) // 1000 if v and v.isdigit() else None


def cpu_type(n):
    # RK3588: 0-3 Cortex-A55, 4-7 Cortex-A76
    part = None
    for line in (read("/proc/cpuinfo") or "").split("\n\n"):
        if re.search(rf"^processor\s*:\s*{n}$", line, re.M):
            m = re.search(r"CPU part\s*:\s*(0x[0-9a-f]+)", line)
            part = m.group(1) if m else None
            break
    return {"0xd05": "A55", "0xd0b": "A76", "0xd03": "A53", "0xd08": "A72"}.get(part, "")


def read_temps():
    res = []
    for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*"), key=natural_key):
        t = read(z + "/temp")
        if t is None:
            continue
        try:
            res.append((read(z + "/type") or os.path.basename(z), int(t) / 1000.0))
        except ValueError:
            pass
    return res


def read_npu():
    """Dönüş: (çekirdek yük listesi, hata mesajı)"""
    if not os.path.exists("/sys/kernel/debug/rknpu"):
        if os.geteuid() != 0:
            return None, "erişilemedi: sudo ile çalıştırın"
        return None, "debugfs yok: mount -t debugfs none /sys/kernel/debug"
    t = read(NPU_LOAD)
    if t is None:
        return None, "okunamadı (sudo?)"
    cores = [int(x) for x in re.findall(r"Core\d+:\s*(\d+)%", t)]
    if not cores:  # eski sürücü formatı: "NPU load: 23%"
        cores = [int(x) for x in re.findall(r"(\d+)%", t)]
    return cores, None


def devfreq(pattern):
    for d in glob.glob(f"/sys/class/devfreq/*{pattern}*"):
        return d
    return None


def read_gpu():
    d = devfreq("gpu")
    if not d:
        return None, None
    load = read(d + "/load")  # "45@800000000Hz"
    pct = None
    if load:
        m = re.match(r"(\d+)", load)
        pct = int(m.group(1)) if m else None
    f = read(d + "/cur_freq")
    mhz = int(f) // 1_000_000 if f and f.isdigit() else None
    return pct, mhz


def read_npu_freq():
    d = devfreq("npu")
    f = read(d + "/cur_freq") if d else None
    return int(f) // 1_000_000 if f and f.isdigit() else None


def read_mem():
    info = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        info[k] = int(v.split()[0]) if v.split() else 0
    tot = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    return tot, tot - avail


def uptime_str():
    v = read("/proc/uptime")
    if not v:
        return "?"
    s = int(float(v.split()[0]))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    return (f"{d}g " if d else "") + f"{h:02d}:{m:02d}"


# ---------------------------------------------------------------- çizim
C_OK, C_WARN, C_CRIT, C_HEAD, C_DIM = 1, 2, 3, 4, 5


def level(v, warn, crit):
    return C_CRIT if v >= crit else C_WARN if v >= warn else C_OK


class Screen:
    def __init__(self, scr):
        self.scr = scr
        self.rows, self.cols = scr.getmaxyx()

    def put(self, y, x, s, attr=0):
        if y < 0 or y >= self.rows - 1 or x >= self.cols:
            return
        try:
            self.scr.addstr(y, x, s[: max(0, self.cols - x - 1)], attr)
        except curses.error:
            pass

    def bar(self, y, x, w, pct, color):
        pct = max(0.0, min(100.0, pct))
        full = int(round(pct / 100.0 * w))
        self.put(y, x, "█" * full, curses.color_pair(color))
        self.put(y, x + full, "░" * (w - full), curses.color_pair(C_DIM))

    @staticmethod
    def spark(hist, w, scale):
        vals = list(hist)[-w:]
        out = []
        for v in vals:
            i = int(round(max(0.0, min(1.0, v / scale)) * (len(SPARK) - 1)))
            out.append(SPARK[i])
        return "".join(out).rjust(w)

    def row(self, y, label, value, text, hist=None, warn=60, crit=85, scale=100.0):
        bw = max(10, min(40, (self.cols - LW - 12) // 2))
        c = level(value, warn, crit)
        self.put(y, 0, label[:LW - 1].ljust(LW))
        self.bar(y, LW, bw, value / scale * 100.0, c)
        self.put(y, LW + bw + 1, text.rjust(9), curses.color_pair(c) | curses.A_BOLD)
        sx = LW + bw + 11
        sw = self.cols - sx - 1
        if hist is not None and sw > 4:
            self.put(y, sx, self.spark(hist, sw, scale), curses.color_pair(c))
        return y + 1

    def header(self, y, title, extra=""):
        self.put(y, 0, f"── {title} ", curses.color_pair(C_HEAD) | curses.A_BOLD)
        if extra:
            self.put(y, len(title) + 4, extra + " ", curses.color_pair(C_DIM))
        return y + 1


# ---------------------------------------------------------------- ana döngü
def run(stdscr, args):
    curses.curs_set(0)
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(C_OK, curses.COLOR_GREEN, bg)
    curses.init_pair(C_WARN, curses.COLOR_YELLOW, bg)
    curses.init_pair(C_CRIT, curses.COLOR_RED, bg)
    curses.init_pair(C_HEAD, curses.COLOR_CYAN, bg)
    curses.init_pair(C_DIM, 8 if curses.COLORS > 8 else curses.COLOR_WHITE, bg)
    stdscr.timeout(100)

    interval = args.interval
    show_cores = True
    paused = False
    host = socket.gethostname()
    hist = {}
    H = lambda k: hist.setdefault(k, deque(maxlen=HIST_LEN))

    cpu = CpuSampler()
    ncpu = len([k for k in cpu.prev if k != "cpu"])
    ctypes = {n: cpu_type(n) for n in range(ncpu)}

    logf = None
    if args.log:
        new = not os.path.exists(args.log)
        logf = open(args.log, "a", buffering=1)
        hdr_written = not new

    data = {}
    last = 0.0
    while True:
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q"), 27):
            break
        elif ch in (ord("+"), ord("=")):
            interval = min(10.0, round(interval + 0.25, 2))
        elif ch in (ord("-"), ord("_")):
            interval = max(0.25, round(interval - 0.25, 2))
        elif ch in (ord("c"), ord("C")):
            show_cores = not show_cores
        elif ch in (ord("p"), ord("P"), ord(" ")):
            paused = not paused
        elif ch == curses.KEY_RESIZE:
            pass

        now = time.monotonic()
        if not paused and now - last >= interval:
            last = now
            data["cpu"] = cpu.sample()
            data["freq"] = {n: cpu_freq_mhz(n) for n in range(ncpu)}
            data["temps"] = read_temps()
            data["npu"], data["npu_err"] = read_npu()
            data["npu_f"] = read_npu_freq()
            data["gpu"], data["gpu_f"] = read_gpu()
            data["mem"] = read_mem()

            H("cpu").append(data["cpu"].get("cpu", 0))
            for n in range(ncpu):
                H(f"cpu{n}").append(data["cpu"].get(f"cpu{n}", 0))
            for i, v in enumerate(data["npu"] or []):
                H(f"npu{i}").append(v)
            if data["npu"]:
                H("npu").append(sum(data["npu"]) / len(data["npu"]))
            if data["gpu"] is not None:
                H("gpu").append(data["gpu"])
            tot, used = data["mem"]
            H("mem").append(100.0 * used / tot if tot else 0)
            for name, t in data["temps"]:
                H("t:" + name).append(t)

            if logf:
                if not hdr_written:
                    cols = (["time", "cpu"] + [f"cpu{n}" for n in range(ncpu)]
                            + [f"npu{i}" for i in range(len(data["npu"] or []))]
                            + ["gpu", "mem"] + [n for n, _ in data["temps"]])
                    logf.write(",".join(cols) + "\n")
                    hdr_written = True
                vals = ([time.strftime("%Y-%m-%d %H:%M:%S"), f"{data['cpu'].get('cpu', 0):.1f}"]
                        + [f"{data['cpu'].get(f'cpu{n}', 0):.1f}" for n in range(ncpu)]
                        + [str(v) for v in (data["npu"] or [])]
                        + [str(data["gpu"] if data["gpu"] is not None else ""),
                           f"{H('mem')[-1]:.1f}"]
                        + [f"{t:.1f}" for _, t in data["temps"]])
                logf.write(",".join(vals) + "\n")

        # -------- çiz
        stdscr.erase()
        s = Screen(stdscr)
        la = (read("/proc/loadavg") or "").split()[:3]
        title = f" rkmon │ {host} │ up {uptime_str()} │ load {' '.join(la)} │ {interval:.2f}s "
        if paused:
            title += "│ DURAKLATILDI "
        s.put(0, 0, title.ljust(s.cols - 1), curses.A_REVERSE | curses.A_BOLD)
        y = 2

        if "cpu" in data:
            # CPU
            y = s.header(y, "CPU", f"{ncpu} çekirdek")
            y = s.row(y, "Toplam", data["cpu"].get("cpu", 0),
                      f"{data['cpu'].get('cpu', 0):5.1f}%", H("cpu"))
            if show_cores:
                for n in range(ncpu):
                    f = data["freq"].get(n)
                    lbl = f"cpu{n} {ctypes[n]:<3} {f if f else '?':>4}MHz"
                    v = data["cpu"].get(f"cpu{n}", 0)
                    y = s.row(y, lbl, v, f"{v:5.1f}%", H(f"cpu{n}"))
            y += 1

            # NPU
            nf = data["npu_f"]
            y = s.header(y, "NPU", f"{nf} MHz" if nf else "")
            if data["npu"] is None:
                s.put(y, 2, data["npu_err"] or "yok", curses.color_pair(C_WARN))
                y += 1
            else:
                for i, v in enumerate(data["npu"]):
                    y = s.row(y, f"Core{i}", v, f"{v:3d}%", H(f"npu{i}"))
            y += 1

            # GPU + RAM
            y = s.header(y, "GPU / RAM")
            if data["gpu"] is not None:
                gf = data["gpu_f"]
                y = s.row(y, f"GPU {gf if gf else '?'}MHz", data["gpu"],
                          f"{data['gpu']:3d}%", H("gpu"))
            tot, used = data["mem"]
            mp = 100.0 * used / tot if tot else 0
            y = s.row(y, f"RAM {used / 1048576:.1f}/{tot / 1048576:.1f}G", mp,
                      f"{mp:5.1f}%", H("mem"), warn=75, crit=90)
            y += 1

            # Sıcaklık
            temps = data["temps"]
            mx = max((t for _, t in temps), default=0)
            y = s.header(y, "SICAKLIK", f"maks {mx:.1f}°C" if temps else "")
            if not temps:
                s.put(y, 2, "thermal_zone bulunamadı", curses.color_pair(C_WARN))
                y += 1
            for name, t in temps:
                y = s.row(y, name, t, f"{t:5.1f}°C", H("t:" + name),
                          warn=args.temp_warn, crit=args.temp_crit, scale=100.0)

        s.put(s.rows - 1, 0,
              " q:çıkış  +/-:aralık  c:çekirdekler  p:duraklat ".ljust(s.cols - 1)[: s.cols - 1],
              curses.A_REVERSE)
        stdscr.refresh()

    if logf:
        logf.close()


def main():
    ap = argparse.ArgumentParser(description="Orange Pi / RK3588 terminal monitörü")
    ap.add_argument("-i", "--interval", type=float, default=1.0, help="yenileme aralığı (sn)")
    ap.add_argument("--log", help="değerleri CSV dosyasına yaz")
    ap.add_argument("--temp-warn", type=float, default=60.0, help="sarı eşik (°C)")
    ap.add_argument("--temp-crit", type=float, default=80.0, help="kırmızı eşik (°C)")
    args = ap.parse_args()
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(run, args)


if __name__ == "__main__":
    main()

import time
import threading
import queue
import base64
from collections import deque
import numpy as np
import cv2
import board
import busio
import adafruit_mlx90640
import RPi.GPIO as GPIO
import requests

import firebase_admin
from firebase_admin import credentials, db


RONG, CAO = 32, 24
TYLE = 20

TMIN, TMAX = 20.0, 80.0
BUOC = 2
TMIN_NGUOI, TMAX_NGUOI = 25.0, 40.0
TEN_CHE_DO = ["TU DONG", f"NGUOI {TMIN_NGUOI:.0f}-{TMAX_NGUOI:.0f}", f"CO DINH {TMIN:.0f}-{TMAX:.0f}"]
che_do = 0
KHOANG_TOI_THIEU = 8.0
LE_THANG = 1.0
HE_SO_MUOT_THANG = 0.15

ALPHA = 0.6
JPEG_Q = 90

T_GUI = 1.0
T_ANH_TOI_THIEU = 0.0

T_ANH_LICH_SU = 1.0
TYLE_LICH_SU = 12
JPEG_Q_LICH_SU = 78


DUONG_DAN_KHOA_FIREBASE = "/home/pi/Documents/serviceAccountKey.json"
URL_DB = "https://camera-mlx90640-default-rtdb.firebaseio.com/"

cred = credentials.Certificate(DUONG_DAN_KHOA_FIREBASE)
firebase_admin.initialize_app(cred, {"databaseURL": URL_DB})

ref_moi = db.reference("hinhanh/moinhat")

ref_giamsat = db.reference("giamsat")
ref_goc = db.reference("/")

ref_giamsat_lichsu = db.reference("giamsat/lichsu")
SO_DIEM_LICH_SU_TOIDA = 60
KHOANG_CACH_LICH_SU = 60.0

DUONG_DAN_NGUONG_CANBAO = "nhietdo/canhbao"
ref_ng = db.reference(DUONG_DAN_NGUONG_CANBAO)

DUONG_DAN_NGUONG_NGUYHIEM = "nhietdo/nguyhiem"
ref_nguyhiem = db.reference(DUONG_DAN_NGUONG_NGUYHIEM)
NGUONG_MAC_DINH = 35.0
NGUONG_NGUYHIEM_MAC_DINH = 60.0
T_LAM_MOI = 3.0
THOI_GIAN_KEU = 3.0

BIEN_DO_TAT_CANHBAO = 2.0
THOI_GIAN_CHO_CANHBAO = 60.0

URL_APPS_SCRIPT = "https://script.google.com/macros/s/AKfycbw0rQpHJ_u_DuJeVTMfl7N9vm-7zNbaoy-1Y4TWQ1P_2aX1HPKtLMBKjHPQKJK4vyRZfw/exec"
T_TIMEOUT_DRIVE = 90.0
SO_ANH_MOI_LO = 60
T_GUI_LO = 60.0
SO_ANH_TOI_DA_MOI_LAN_GUI = 120
SO_ANH_BO_DEM_TOI_DA = 1800
T_CHO_SAU_LOI = 15.0
SO_LAN_THU_LAI = 3
T_CHO_THU_LAI = 2.0

chay = True
lan_cap_ls = 0.0
lan_gui_lichsu = 0.0
lan_gui_anh = 0.0
hang_doi_lichsu = deque(maxlen=SO_DIEM_LICH_SU_TOIDA)
tmax_trong_ky = None

try:
    du_lieu_cu = ref_giamsat_lichsu.get()
    if isinstance(du_lieu_cu, list):
        ts_giu_cuoi = None
        for diem in du_lieu_cu:
            if isinstance(diem, dict) and "timestamp" in diem and "max_temp" in diem:
                if ts_giu_cuoi is None or diem["timestamp"] - ts_giu_cuoi >= (KHOANG_CACH_LICH_SU - 2) * 1000:
                    hang_doi_lichsu.append(diem)
                    ts_giu_cuoi = diem["timestamp"]
except Exception as e:
    print(f"[Firebase] Khong nap duoc lich su cu: {e}")

khoa_fb = threading.Lock()

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.OUT, initial=GPIO.LOW)

nguong = NGUONG_MAC_DINH
nguong_nguyhiem = NGUONG_NGUYHIEM_MAC_DINH
da_bao_thieu_nguyhiem = False
lan_doc = 0.0

da_kich_hoat = False
t_bat_dau = 0.0
da_chan_doan_nguong = False


def doc_nguong():
    global nguong, nguong_nguyhiem, lan_doc, da_chan_doan_nguong, da_bao_thieu_nguyhiem

    now = time.time()
    if now - lan_doc < T_LAM_MOI:
        return nguong, nguong_nguyhiem

    lan_doc = now
    try:
        gt = ref_ng.get()

        if isinstance(gt, dict):
            gt = gt.get("value", gt.get("threshold"))

        if gt is not None:
            ng_moi = float(gt)
            if np.isfinite(ng_moi) and -40.0 <= ng_moi <= 300.0:
                nguong = ng_moi
            else:
                print(f"[Firebase] Ngưỡng không hợp lệ: {gt!r}; giữ {nguong:.1f} C")
        else:
            print(f"[Firebase] Chưa có {DUONG_DAN_NGUONG_CANBAO}; giữ mặc định {nguong:.1f} C")

            if not da_chan_doan_nguong:
                da_chan_doan_nguong = True
                try:
                    cha = db.reference("nhietdo").get()
                    goc = db.reference("/").get(shallow=True)
                    print(f"[Firebase] Chan doan: DB={URL_DB}")
                    print(f"[Firebase] Chan doan: nut goc Pi thay = {sorted(goc) if isinstance(goc, dict) else goc!r}")
                    print(f"[Firebase] Chan doan: nhietdo = {cha!r}")
                except Exception as e2:
                    print(f"[Firebase] Chan doan that bai: {e2}")

    except Exception as e:
        print(f"[Firebase] Không đọc được ngưỡng cảnh báo: {e}; giữ {nguong:.1f} C")

    try:
        gt = ref_nguyhiem.get()

        if isinstance(gt, dict):
            gt = gt.get("value", gt.get("threshold"))

        if gt is not None:
            ng_moi = float(gt)
            if np.isfinite(ng_moi) and -40.0 <= ng_moi <= 300.0:
                nguong_nguyhiem = ng_moi
                da_bao_thieu_nguyhiem = False
            else:
                print(f"[Firebase] Ngưỡng bật chuông không hợp lệ: {gt!r}; giữ {nguong_nguyhiem}")
        else:
            nguong_nguyhiem = NGUONG_NGUYHIEM_MAC_DINH
            if not da_bao_thieu_nguyhiem:
                da_bao_thieu_nguyhiem = True
                print(f"[Firebase] Chưa có {DUONG_DAN_NGUONG_NGUYHIEM}; chuông dùng mặc định {NGUONG_NGUYHIEM_MAC_DINH:.1f} C")

    except Exception as e:
        print(f"[Firebase] Không đọc được ngưỡng bật chuông: {e}; giữ {nguong_nguyhiem}")

    return nguong, nguong_nguyhiem


def bao_chuong(tmax):
    global da_kich_hoat, t_bat_dau

    ng_cb, ng_chuong = doc_nguong()

    vuot = float(tmax) >= ng_chuong
    now = time.time()

    if vuot:
        if not da_kich_hoat:
            da_kich_hoat = True
            t_bat_dau = now
    else:
        da_kich_hoat = False

    dang_keu = da_kich_hoat and (now - t_bat_dau < THOI_GIAN_KEU)

    GPIO.output(17, GPIO.HIGH if dang_keu else GPIO.LOW)
    return ng_cb, ng_chuong, dang_keu


THAM_SO_444 = []
if hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR") and hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444"):
    THAM_SO_444 = [cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]


def ma_hoa_b64(anh, ql=JPEG_Q):
    ok, buf = cv2.imencode(".jpg", anh, [cv2.IMWRITE_JPEG_QUALITY, ql, cv2.IMWRITE_JPEG_OPTIMIZE, 1] + THAM_SO_444)

    if not ok:
        return None

    return base64.b64encode(buf).decode("utf-8")


hang_doi_anh_ls = queue.Queue()


def gui_lo_len_drive(frames):
    payload = {
        "batch": {
            "frames": [
                {"ts": ts, "max": round(tmax, 1), "tmin": round(t0, 1), "tmax": round(t1, 1), "d": b64}
                for (b64, tmax, ts, t0, t1) in frames
            ]
        }
    }

    for lan in range(1, SO_LAN_THU_LAI + 1):
        try:
            r = requests.post(URL_APPS_SCRIPT, json=payload, timeout=T_TIMEOUT_DRIVE)
            r.raise_for_status()

            kq = r.json()
            if kq.get("status") == "ok" and kq.get("saved") == len(frames):
                print(f"[Drive] Đã gửi lô {len(frames)} ảnh: {kq.get('name')}")
                return True

            print(f"[Drive] Apps Script báo lỗi (lần {lan}/{SO_LAN_THU_LAI}): {kq.get('message') or kq}")

        except Exception as e:
            print(f"[Drive] Loi khi gui lo {len(frames)} anh (lan {lan}/{SO_LAN_THU_LAI}): {e}")

        if lan < SO_LAN_THU_LAI:
            time.sleep(T_CHO_THU_LAI * lan)

    return False


def worker_drive():
    lo = []
    t_dau_lo = 0.0
    khong_gui_truoc = 0.0

    while chay:
        try:
            item = hang_doi_anh_ls.get(timeout=1)
            if not lo:
                t_dau_lo = time.time()
            lo.append(item)
        except queue.Empty:
            pass

        if len(lo) > SO_ANH_BO_DEM_TOI_DA:
            so_bo = len(lo) - SO_ANH_BO_DEM_TOI_DA
            del lo[:so_bo]
            print(f"[Drive] Bo dem day, bo {so_bo} anh cu nhat")

        if not lo or time.time() < khong_gui_truoc:
            continue

        du_so_luong = len(lo) >= SO_ANH_MOI_LO
        het_gio = (time.time() - t_dau_lo) >= T_GUI_LO
        if not (du_so_luong or het_gio):
            continue

        chunk = lo[:SO_ANH_TOI_DA_MOI_LAN_GUI]
        if gui_lo_len_drive(chunk):
            del lo[:len(chunk)]
            t_dau_lo = time.time()
        else:
            khong_gui_truoc = time.time() + T_CHO_SAU_LOI
            print(f"[Drive] Giu lai {len(lo)} anh, thu gui lai sau {T_CHO_SAU_LOI:.0f}s")


t_drive = threading.Thread(target=worker_drive, daemon=True)
t_drive.start()


def luong_gui():
    global canhbao_moi, canhbao_tat_moi, lan_gui_lichsu, tmax_trong_ky, lan_gui_anh

    while chay:
        t_batdau_ck = time.time()

        with khoa:
            if anh_ht is None or tmax_ht is None:
                anh = None
            else:
                anh = anh_ht.copy()
                tmax = tmax_ht
                tavg = tavg_ht
                co_canhbao_moi = canhbao_moi
                canhbao_moi = False

                if co_canhbao_moi:
                    co_canhbao_tat = False
                else:
                    co_canhbao_tat = canhbao_tat_moi
                    canhbao_tat_moi = False

        if anh is not None:
            now_a = time.time()
            gui_anh = (now_a - lan_gui_anh >= T_ANH_TOI_THIEU)
            b64 = ma_hoa_b64(anh) if gui_anh else None

            if (not gui_anh) or b64 is not None:
                ts = int(time.time() * 1000)

                data_anh = {
                    "data": b64,
                    "timestamp": ts,
                }

                data_giamsat = {
                    "max_temp": round(tmax, 1),
                    "avg_temp": round(tavg, 1),
                    "timestamp": ts,
                }
                if co_canhbao_moi:
                    data_giamsat["canhbao"] = True
                elif co_canhbao_tat:
                    data_giamsat["canhbao"] = False

                try:
                    with khoa_fb:
                        cap_nhat = {f"giamsat/{k}": v for k, v in data_giamsat.items()}
                        if b64 is not None:
                            cap_nhat["hinhanh/moinhat"] = data_anh
                        ref_goc.update(cap_nhat)
                        if b64 is not None:
                            lan_gui_anh = now_a

                    print(f"[Firebase] Đã gửi {'ảnh + ' if b64 is not None else ''}giám sát (MAX: {tmax:.1f}°C, AVG: {tavg:.1f}°C"
                          + (", CẢNH BÁO MỚI: true)" if co_canhbao_moi
                             else ", HẾT CẢNH BÁO: false)" if co_canhbao_tat else ")"))

                except Exception as e:
                    print(f"[Firebase] Loi khi gui du lieu: {e}")

                now3 = time.time()
                tmax_trong_ky = tmax if tmax_trong_ky is None else max(tmax_trong_ky, tmax)
                if now3 - lan_gui_lichsu >= KHOANG_CACH_LICH_SU:
                    lan_gui_lichsu = now3
                    hang_doi_lichsu.append({"timestamp": ts, "max_temp": round(tmax_trong_ky, 1), "avg_temp": round(tavg, 1)})
                    tmax_trong_ky = None
                    try:
                        with khoa_fb:
                            ref_giamsat_lichsu.set(list(hang_doi_lichsu))
                    except Exception as e:
                        print(f"[Firebase] Loi khi gui lich su: {e}")

        co_khung_moi.wait(timeout=T_GUI)
        co_khung_moi.clear()
        su_kien_canhbao.clear()


i2c = busio.I2C(board.SCL, board.SDA)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ


khung = np.zeros(RONG * CAO, dtype=np.float32)
anh_moi = np.zeros((CAO, RONG), dtype=np.float32)

anh_ht = None
tmax_ht = None
tavg_ht = None
canhbao_moi = False
canhbao_tat_moi = False
canhbao_dang_mo = False
lan_gui_canhbao_cuoi = 0.0

khoa = threading.Lock()
su_kien_canhbao = threading.Event()
co_khung_moi = threading.Event()
co_khung_hien_thi = threading.Event()


def luong_doc():
    global anh_moi

    while chay:
        try:
            mlx.getFrame(khung)

        except ValueError:
            continue

        except RuntimeError:
            time.sleep(0.1)
            continue

        with khoa:
            anh_moi = np.reshape(khung, (CAO, RONG)).copy()
        co_khung_moi.set()
        co_khung_hien_thi.set()


t_doc = threading.Thread(target=luong_doc, daemon=True)
t_doc.start()

t_fb = threading.Thread(target=luong_gui, daemon=True)
t_fb.start()


sharp_on = True

HE_SO_TUONG_PHAN = 1.0
DO_SANG = 0


def lam_net(anh, sigma=1.0, amt=0.55):
    mo = cv2.GaussianBlur(anh, (0, 0), sigma)
    net = cv2.addWeighted(anh, 1.0 + amt, mo, -amt, 0)
    return np.clip(net, 0, 255).astype(np.uint8)


def xu_ly_xam(anh):
    kq = anh.copy()

    if HE_SO_TUONG_PHAN != 1.0 or DO_SANG != 0:
        kq = cv2.convertScaleAbs(kq, alpha=HE_SO_TUONG_PHAN, beta=DO_SANG)

    if sharp_on:
        kq = lam_net(kq, sigma=1.0, amt=0.45)

    return kq


BANG_VACH = {
    0.2: (0.1, 1.0),
    0.5: (0.1, 2.0),
    1.0: (0.2, 5.0),
    2.0: (1.0, 10.0),
    5.0: (1.0, 20.0),
    10.0: (2.0, 50.0),
    20.0: (5.0, 100.0),
}


def thanh_mau(cao, tmin, tmax, buoc=None, w_bar=48, w_lb=58, pad=15):
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = 0.40

    w = w_lb + w_bar + w_lb
    x0 = w_lb
    x1 = w_lb + w_bar
    nen = np.full((cao, w, 3), 25, dtype=np.uint8)

    h_bar = max(1, cao - 2 * pad)

    grad = np.linspace(255, 0, h_bar).astype(np.uint8)
    grad = np.repeat(grad.reshape(-1, 1), w_bar, axis=1)
    bar = cv2.applyColorMap(grad, cv2.COLORMAP_TURBO)

    nen[pad:pad + h_bar, x0:x1] = bar

    cv2.rectangle(nen, (x0, pad), (x1 - 1, pad + h_bar - 1), (210, 210, 210), 1)

    khoang = float(tmax - tmin)
    px_moi_do = h_bar / khoang

    if buoc is None:
        buoc = 20.0
        for b in sorted(BANG_VACH):
            if px_moi_do * b >= 11.0:
                buoc = b
                break

    sub, noi = BANG_VACH.get(float(buoc), (buoc / 2.0, buoc * 5.0))
    kl = max(1, int(round(buoc / sub)))
    kn = max(1, int(round(noi / sub)))
    zigzag = (px_moi_do * buoc) < 18.0
    co_le = buoc < 1.0

    k0 = int(np.ceil(tmin / sub - 1e-9))
    k1 = int(np.floor(tmax / sub + 1e-9))

    n_nhan = 0
    for k in range(k0, k1 + 1):
        t = k * sub
        ty = (t - tmin) / khoang
        y = int(pad + h_bar - ty * h_bar)
        y = max(pad, min(pad + h_bar - 1, y))

        chinh = (k % kl == 0)
        muc = (k % kn == 0)
        ldai = 12 if muc else (8 if chinh else 4)
        dday = 2 if muc else 1

        cv2.line(nen, (x1, y), (x1 + ldai, y), (255, 255, 255), dday, cv2.LINE_AA)
        cv2.line(nen, (x0 - 1, y), (x0 - 1 - ldai, y), (255, 255, 255), dday, cv2.LINE_AA)

        if chinh:
            nhan = f"{t:.1f} C" if co_le else f"{t:.0f} C"
            mau_chu = (0, 255, 255) if muc else (255, 255, 255)
            yc = max(12, min(cao - 4, y + 4))

            ben_phai = (not zigzag) or (n_nhan % 2 == 0)
            if ben_phai:
                xc = x1 + ldai + 3
            else:
                (tw, _), _ = cv2.getTextSize(nhan, font, fs, 1)
                xc = max(0, x0 - ldai - 3 - tw)

            cv2.putText(nen, nhan, (xc, yc), font, fs, mau_chu, 1, cv2.LINE_AA)
            n_nhan += 1

    return nen


def ve_max(mau, nhiet):
    vmin, vmax, lmin, lmax = cv2.minMaxLoc(nhiet)

    x = int(lmax[0] * TYLE + TYLE / 2)
    y = int(lmax[1] * TYLE + TYLE / 2)

    cv2.drawMarker(mau, (x, y), (255, 255, 255), cv2.MARKER_CROSS, 28, 2, cv2.LINE_AA)

    nhan = f"MAX {vmax:.1f} C"

    xc = min(max(5, x + 12), mau.shape[1] - 120)
    yc = max(22, y - 12)

    (tw, th), _ = cv2.getTextSize(nhan, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)

    cv2.rectangle(mau, (xc - 5, yc - th - 6), (xc + tw + 5, yc + 5), (20, 20, 20), -1)
    cv2.putText(mau, nhan, (xc, yc), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)

    return mau, vmin, vmax


cv2.namedWindow("Thermal Camera", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Thermal Camera", RONG * TYLE + 206, CAO * TYLE + 100)


du_lieu_muot = None
tu_dong_min = None
tu_dong_max = None

try:
    while True:

        if not co_khung_hien_thi.wait(timeout=0.2):
            if (cv2.waitKey(1) & 0xFF) == ord('q'):
                break
            continue
        co_khung_hien_thi.clear()

        with khoa:
            tho = anh_moi.copy()

        tho = tho.astype(np.float32)

        t_do_min = float(tho.min())
        t_do_max = float(tho.max())
        t_do_tb = float(tho.mean())

        if du_lieu_muot is None:
            du_lieu_muot = tho.copy()
        else:
            du_lieu_muot = du_lieu_muot * (1.0 - ALPHA) + tho * ALPHA

        du_lieu_loc = du_lieu_muot

        cmin = t_do_min
        cmax = t_do_max
        ctb = t_do_tb

        big = cv2.resize(du_lieu_loc, (RONG * TYLE, CAO * TYLE), interpolation=cv2.INTER_CUBIC)
        big = cv2.GaussianBlur(big, (5, 5), 0)

        if che_do == 2:
            dmin, dmax = TMIN, TMAX
            buoc_thang = BUOC
        elif che_do == 1:
            dmin, dmax = TMIN_NGUOI, TMAX_NGUOI
            buoc_thang = 1.0
        else:
            muc_tieu_min = float(np.percentile(du_lieu_loc, 1)) - LE_THANG
            muc_tieu_max = max(float(du_lieu_loc.max()), t_do_max) + LE_THANG
            if muc_tieu_max - muc_tieu_min < KHOANG_TOI_THIEU:
                tt = (muc_tieu_min + muc_tieu_max) / 2.0
                muc_tieu_min = tt - KHOANG_TOI_THIEU / 2.0
                muc_tieu_max = tt + KHOANG_TOI_THIEU / 2.0

            if tu_dong_min is None:
                tu_dong_min, tu_dong_max = muc_tieu_min, muc_tieu_max
            else:
                tu_dong_min += (muc_tieu_min - tu_dong_min) * HE_SO_MUOT_THANG
                tu_dong_max += (muc_tieu_max - tu_dong_max) * HE_SO_MUOT_THANG

            dmin, dmax = tu_dong_min, tu_dong_max
            buoc_thang = None

        norm = np.clip((big - dmin) / (dmax - dmin), 0, 1)
        img8 = (norm * 255).astype(np.uint8)
        img8 = xu_ly_xam(img8)

        mau = cv2.applyColorMap(img8, cv2.COLORMAP_TURBO)

        mau, vmin, vmax = ve_max(mau, tho)

        ng_ht, ng_chuong_ht, bao = bao_chuong(vmax)

        cv2.rectangle(mau, (0, 0), (mau.shape[1] - 1, mau.shape[0] - 1), (255, 255, 255), 2, cv2.LINE_AA)

        now_ls = time.time()
        if now_ls - lan_cap_ls >= T_ANH_LICH_SU:
            lan_cap_ls = now_ls
            nho = cv2.resize(mau, (RONG * TYLE_LICH_SU, CAO * TYLE_LICH_SU), interpolation=cv2.INTER_AREA)
            b64_ls = ma_hoa_b64(nho, JPEG_Q_LICH_SU)
            if b64_ls is not None:
                hang_doi_anh_ls.put((b64_ls, float(vmax), int(now_ls * 1000), float(dmin), float(dmax)))

        hbar = mau.shape[0]
        bar = thanh_mau(hbar, dmin, dmax, buoc_thang)

        gap = np.full((hbar, 18, 3), 20, dtype=np.uint8)
        ghep = np.hstack([mau, gap, bar])

        h_fb = 42
        tieu_fb = np.full((h_fb, ghep.shape[1], 3), 20, dtype=np.uint8)
        chu_fb = f"MIN: {cmin:.1f} C    MAX: {cmax:.1f} C"
        cv2.putText(tieu_fb, chu_fb, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2, cv2.LINE_AA)

        anh_fb = np.vstack([tieu_fb, ghep])

        with khoa:
            anh_ht = anh_fb.copy()
            tmax_ht = cmax
            tavg_ht = ctb

        now_cb = time.time()
        if not canhbao_dang_mo:
            if vmax >= ng_ht and (now_cb - lan_gui_canhbao_cuoi >= THOI_GIAN_CHO_CANHBAO):
                canhbao_dang_mo = True
                lan_gui_canhbao_cuoi = now_cb
                with khoa:
                    canhbao_moi = True
                su_kien_canhbao.set()
        else:
            if vmax < ng_ht - BIEN_DO_TAT_CANHBAO:
                canhbao_dang_mo = False
                with khoa:
                    canhbao_tat_moi = True
                su_kien_canhbao.set()

        h_top = 48
        top = np.full((h_top, ghep.shape[1], 3), 20, dtype=np.uint8)
        cv2.putText(top, "THERMAL CAMERA - MLX90640", (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(top, f"THANG: {TEN_CHE_DO[che_do]}  [f]", (500, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)

        h_bot = 34
        bot = np.full((h_bot, ghep.shape[1], 3), 20, dtype=np.uint8)

        gio = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(bot, gio, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (180, 180, 180), 1, cv2.LINE_AA)

        mode = (
            f"SHARP:{'ON' if sharp_on else 'OFF'}  "
            f"HE_SO_TUONG_PHAN:{HE_SO_TUONG_PHAN:.1f}  |  "
            f"CANHBAO:{ng_ht:.1f}C  "
            f"CHUONG@{ng_chuong_ht:.1f}C:{'ON' if bao else 'OFF'}"
        )
        cv2.putText(bot, mode, (235, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1, cv2.LINE_AA)

        final = np.vstack([top, ghep, bot])

        cv2.imshow("Thermal Camera", final)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break
        elif key == ord('s'):
            sharp_on = not sharp_on
            print(f"[Che do] Lam net (Sharpen): {'BAT' if sharp_on else 'TAT'}")
        elif key == ord('f'):
            che_do = (che_do + 1) % len(TEN_CHE_DO)
            tu_dong_min = tu_dong_max = None
            print(f"[Che do] Thang mau: {TEN_CHE_DO[che_do]}")

except KeyboardInterrupt:
    pass

finally:
    chay = False

    t_doc.join(timeout=2)
    t_fb.join(timeout=2)

    cv2.destroyAllWindows()
    GPIO.output(17, GPIO.LOW)
    GPIO.cleanup()

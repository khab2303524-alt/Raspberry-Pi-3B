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

# Thang màu CỐ ĐỊNH cho giám sát pin xe điện: màu trên ảnh ứng đúng nhiệt độ thực,
# không co giãn theo từng khung. Mặc định đang dùng thang TỰ ĐỘNG; phím 'f' chuyển sang thang cố định này.
TMIN, TMAX = 20.0, 80.0
BUOC = 2  # ghi nhãn mỗi 2 °C (vạch chia nhỏ mỗi 1 °C); nhãn sát nhau sẽ tự ghi zic zac 2 bên
thang_co_dinh = False  # mặc định: thang TỰ ĐỘNG (bấm f để chuyển sang thang cố định)

ALPHA = 0.25
JPEG_Q = 88
GIOI_HAN_CHI_SO = 100  # Drive chỉ giữ tối đa 100 ảnh: frame_0000 … frame_0099

T_GUI = 1.0
T_GUI_DRIVE = 5.0  # gửi ảnh lên Drive thưa hơn nhịp Firebase, tránh dồn ứ hàng đợi


DUONG_DAN_KHOA_FIREBASE = "serviceAccountKey.json"
URL_DB = "https://camera-mlx90640-default-rtdb.firebaseio.com/"

cred = credentials.Certificate(DUONG_DAN_KHOA_FIREBASE)
firebase_admin.initialize_app(cred, {"databaseURL": URL_DB})

ref_moi = db.reference("hinhanh/moinhat")

# Nhánh riêng cho trạng thái giám sát (nhiệt độ max + cảnh báo),
# tách khỏi hinhanh/moinhat để không gửi chung với ảnh.
ref_giamsat = db.reference("giamsat")

# Firebase "giamsat" chỉ giữ giá trị MỚI NHẤT. Để app vẽ được biểu đồ lịch sử
# kể cả khi không mở app lúc đó, thiết bị tự lưu 1 mảng lịch sử (tối đa
# SO_DIEM_LICH_SU_TOIDA điểm, cách nhau KHOANG_CACH_LICH_SU giây) lên
# "giamsat/lichsu". Hai hằng số này PHẢI khớp SO_DIEM_TOIDA/KHOANG_CACH_DIEM_MS
# bên analytics.tsx để 2 bên hiểu cùng một "cửa sổ" thời gian.
ref_giamsat_lichsu = db.reference("giamsat/lichsu")
SO_DIEM_LICH_SU_TOIDA = 60   # 60 điểm x 1 phút = 1 giờ gần nhất
KHOANG_CACH_LICH_SU = 60.0  # giây

DUONG_DAN_NGUONG_CANBAO = "nhietdo/canhbao"
ref_ng = db.reference(DUONG_DAN_NGUONG_CANBAO)

# Hai ngưỡng TÁCH RIÊNG:
#   nhietdo/canhbao  -> ngưỡng cảnh báo: bật cờ giamsat/canhbao báo lên app (đường "cảnh báo" trên biểu đồ)
#   nhietdo/nguyhiem -> ngưỡng bật chuông trên thiết bị (đường "nguy hiểm" trên biểu đồ)
# Chuông CHỈ phụ thuộc nhietdo/nguyhiem; chưa có thì dùng NGUONG_NGUYHIEM_MAC_DINH (không dùng canhbao).
DUONG_DAN_NGUONG_NGUYHIEM = "nhietdo/nguyhiem"
ref_nguyhiem = db.reference(DUONG_DAN_NGUONG_NGUYHIEM)
NGUONG_MAC_DINH = 35.0           # mặc định ngưỡng cảnh báo (khi chưa có nhietdo/canhbao)
NGUONG_NGUYHIEM_MAC_DINH = 60.0  # mặc định ngưỡng bật chuông (khi chưa có nhietdo/nguyhiem)
T_LAM_MOI = 3.0
THOI_GIAN_KEU = 3.0

# Cấu hình chống spam cho cờ canhbao (dành riêng cho việc báo app, tách khỏi chuông vật lý):
# - phải tụt xuống dưới (ngưỡng - BIEN_DO_TAT_CANHBAO) mới coi là hết cảnh báo, tránh
#   nhiệt độ dao động sát ngưỡng làm bật/tắt liên tục
# - dù có vượt ngưỡng lại, vẫn phải cách lần báo trước tối thiểu THOI_GIAN_CHO_CANHBAO giây
BIEN_DO_TAT_CANHBAO = 2.0
THOI_GIAN_CHO_CANHBAO = 60.0

# Ảnh lịch sử được gửi lên Google Drive qua Google Apps Script Web App
# thay vì Firebase. Dán URL Web App sau khi deploy (Deploy > New deployment > Web app).
URL_APPS_SCRIPT = "https://script.google.com/macros/s/AKfycbwTGjhKV6rZc5vZkJ-sdargX334bN7t9Gq60cJjm2Diour_4LZEQyCJdK1GmbnZfVteYQ/exec"
T_TIMEOUT_DRIVE = 30.0   # Apps Script có thể chậm (khởi động nguội, xóa + tạo + đặt quyền)
SO_LAN_THU_LAI = 3       # số lần thử gửi 1 ảnh trước khi bỏ
T_CHO_THU_LAI = 2.0      # giây chờ giữa các lần thử (nhân theo số lần)

FILE_CHI_SO = "chi_so.txt"

def doc_chi_so_da_luu():
    try:
        with open(FILE_CHI_SO, "r") as f:
            gt = int(f.read().strip())
            if 0 <= gt < GIOI_HAN_CHI_SO:
                return gt
    except (FileNotFoundError, ValueError):
        pass
    return 0


def luu_chi_so(gt):
    try:
        with open(FILE_CHI_SO, "w") as f:
            f.write(str(gt))
    except Exception as e:
        print(f"[ChiSo] Loi khi luu chi so: {e}")


chiso = doc_chi_so_da_luu()
chay = True
lan_gui_drive = 0.0
lan_gui_lichsu = 0.0
hang_doi_lichsu = deque(maxlen=SO_DIEM_LICH_SU_TOIDA)
tmax_trong_ky = None  # Tmax lớn nhất trong kỳ 1 phút hiện tại (để không bỏ sót đỉnh nhiệt ngắn)

# Nạp lại lịch sử đã lưu (nếu có) để khởi động lại thiết bị không làm mất
# hẳn biểu đồ đang có trên app.
try:
    du_lieu_cu = ref_giamsat_lichsu.get()
    if isinstance(du_lieu_cu, list):
        # Lịch sử cũ có thể dày hơn (mỗi 10s): lấy thưa lại cho đúng nhịp mới.
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

nguong = NGUONG_MAC_DINH       # ngưỡng cảnh báo (nhietdo/canhbao)
nguong_nguyhiem = NGUONG_NGUYHIEM_MAC_DINH  # ngưỡng bật chuông (nhietdo/nguyhiem)
da_bao_thieu_nguyhiem = False  # chỉ báo 1 lần khi chưa có nhietdo/nguyhiem
lan_doc = 0.0

da_kich_hoat = False
t_bat_dau = 0.0
da_chan_doan_nguong = False  # chỉ in chẩn đoán 1 lần khi không đọc được ngưỡng


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

            # Chẩn đoán 1 lần: in ra những gì Pi THỰC SỰ thấy trong database,
            # để so với Firebase console (sai database/URL, hoặc sai tên khóa).
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

    # --- Ngưỡng bật chuông: nhietdo/nguyhiem ---
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
            # Chưa có (hoặc đã bị xóa) -> chuông dùng ngưỡng mặc định, KHÔNG dùng ngưỡng cảnh báo.
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

    # Chuông reo theo ngưỡng bật chuông (nhietdo/nguyhiem), KHÔNG theo ngưỡng cảnh báo.
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


def ma_hoa_b64(anh, ql=JPEG_Q):
    ok, buf = cv2.imencode(".jpg", anh, [cv2.IMWRITE_JPEG_QUALITY, ql, cv2.IMWRITE_JPEG_OPTIMIZE, 1])

    if not ok:
        return None

    return base64.b64encode(buf).decode("utf-8")


def gui_len_drive(b64, tmax, ts, ten_file):
    payload = {
        "filename": ten_file,
        "data": b64,
        "max_temp": round(tmax, 1),
        "timestamp": ts,
    }

    # Gửi lại an toàn vì Apps Script xóa file cùng tên trước khi tạo mới.
    for lan in range(1, SO_LAN_THU_LAI + 1):
        try:
            r = requests.post(URL_APPS_SCRIPT, json=payload, timeout=T_TIMEOUT_DRIVE)
            r.raise_for_status()

            kq = r.json()
            if kq.get("status") == "ok":
                print(f"[Drive] Đã gửi ảnh lịch sử: {ten_file}")
                return True

            print(f"[Drive] Apps Script báo lỗi (lần {lan}/{SO_LAN_THU_LAI}): {kq.get('message')}")

        except Exception as e:
            print(f"[Drive] Loi khi gui {ten_file} (lan {lan}/{SO_LAN_THU_LAI}): {e}")

        if lan < SO_LAN_THU_LAI:
            time.sleep(T_CHO_THU_LAI * lan)

    return False


# Hàng đợi không giới hạn: giữ đủ mọi ảnh để gửi lên Drive tuần tự,
# không bỏ ảnh nào ngay cả khi luồng gửi đang bận (Drive chậm hơn nhịp chụp).
CANH_BAO_TON_DONG = 30  # số ảnh tồn đọng thì cảnh báo Drive không theo kịp
hang_doi_drive = queue.Queue()


def worker_drive():
    # Luồng này là nơi DUY NHẤT cấp/tăng chiso. Chỉ tăng khi gửi thành công,
    # nên ảnh lỗi không tạo "lỗ trống" trong vòng 100 ô trên Drive.
    global chiso

    while chay:
        try:
            b64, tmax, ts = hang_doi_drive.get(timeout=1)
        except queue.Empty:
            continue

        ten_file = f"frame_{chiso:04d}.jpg"

        if gui_len_drive(b64, tmax, ts, ten_file):
            chiso = (chiso + 1) % GIOI_HAN_CHI_SO
            luu_chi_so(chiso)
        else:
            print(f"[Drive] Bo anh sau {SO_LAN_THU_LAI} lan that bai; giu nguyen o {ten_file} cho anh ke tiep")


t_drive = threading.Thread(target=worker_drive, daemon=True)
t_drive.start()


def luong_gui():
    global lan_gui_drive, canhbao_moi, canhbao_tat_moi, lan_gui_lichsu, tmax_trong_ky

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

                # Nếu vừa có cảnh báo mới thì gửi true trước; cờ "hết cảnh báo" được giữ
                # lại để gửi false ở nhịp kế tiếp (không làm mất cảnh báo ngắn).
                if co_canhbao_moi:
                    co_canhbao_tat = False
                else:
                    co_canhbao_tat = canhbao_tat_moi
                    canhbao_tat_moi = False

        if anh is not None:
            b64 = ma_hoa_b64(anh)

            if b64 is not None:
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
                # canhbao=true đúng lúc mới vượt ngưỡng (cạnh lên);
                # canhbao=false khi nhiệt độ đã tụt xuống dưới ngưỡng (cạnh xuống,
                # có hysteresis BIEN_DO_TAT_CANHBAO để tránh bật/tắt liên tục).
                if co_canhbao_moi:
                    data_giamsat["canhbao"] = True
                elif co_canhbao_tat:
                    data_giamsat["canhbao"] = False

                try:
                    with khoa_fb:
                        ref_moi.set(data_anh)
                        ref_giamsat.update(data_giamsat)

                    print(f"[Firebase] Đã gửi ảnh mới nhất + giám sát (MAX: {tmax:.1f}°C, AVG: {tavg:.1f}°C"
                          + (", CẢNH BÁO MỚI: true)" if co_canhbao_moi
                             else ", HẾT CẢNH BÁO: false)" if co_canhbao_tat else ")"))

                except Exception as e:
                    print(f"[Firebase] Loi khi gui du lieu: {e}")

                # Lưu thêm vào lịch sử (nhịp thưa hơn) để app đọc được biểu đồ
                # ngay cả khi lúc thiết bị ghi nhận điểm đó không có ai mở app.
                now3 = time.time()
                tmax_trong_ky = tmax if tmax_trong_ky is None else max(tmax_trong_ky, tmax)
                if now3 - lan_gui_lichsu >= KHOANG_CACH_LICH_SU:
                    lan_gui_lichsu = now3
                    # Mỗi phút 1 điểm: max_temp là đỉnh trong cả phút đó, avg_temp là giá trị tại thời điểm ghi.
                    hang_doi_lichsu.append({"timestamp": ts, "max_temp": round(tmax_trong_ky, 1), "avg_temp": round(tavg, 1)})
                    tmax_trong_ky = None
                    try:
                        with khoa_fb:
                            ref_giamsat_lichsu.set(list(hang_doi_lichsu))
                    except Exception as e:
                        print(f"[Firebase] Loi khi gui lich su: {e}")

                # Chỉ đẩy lên Drive theo nhịp T_GUI_DRIVE (thưa hơn Firebase),
                # tránh gửi dồn dập làm hàng đợi tồn đọng ngày càng nhiều.
                now2 = time.time()
                if now2 - lan_gui_drive >= T_GUI_DRIVE:
                    lan_gui_drive = now2

                    hang_doi_drive.put((b64, tmax, ts))

                    ton_dong = hang_doi_drive.qsize()
                    if ton_dong >= CANH_BAO_TON_DONG:
                        print(f"[Drive] Canh bao: hang doi ton dong {ton_dong} anh, Drive dang gui khong kip")

        # Trừ đi thời gian đã xử lý để giữ nhịp gửi sát T_GUI giây,
        # nhưng thức dậy ngay nếu trạng thái chuông vừa đổi (canhbao cần cập nhật gấp)
        da_dung = time.time() - t_batdau_ck
        if su_kien_canhbao.wait(timeout=max(0.0, T_GUI - da_dung)):
            su_kien_canhbao.clear()


i2c = busio.I2C(board.SCL, board.SDA)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ


khung = np.zeros(RONG * CAO, dtype=np.float32)
anh_moi = np.zeros((CAO, RONG), dtype=np.float32)

anh_ht = None
tmax_ht = None
tavg_ht = None  # nhiệt độ trung bình của cả khung hình
canhbao_moi = False  # cờ chờ gửi: có cảnh báo MỚI cần báo lên Firebase cho app
canhbao_tat_moi = False  # cờ chờ gửi: vừa HẾT cảnh báo, cần ghi canhbao=false lên Firebase
canhbao_dang_mo = False  # đang có một đợt cảnh báo "mở" (đã báo, chưa tụt hẳn xuống dưới ngưỡng - biên độ)
lan_gui_canhbao_cuoi = 0.0  # thời điểm gửi canhbao=true gần nhất, để chặn spam

khoa = threading.Lock()
su_kien_canhbao = threading.Event()  # đánh thức luồng gửi ngay khi có cảnh báo mới


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


# Bảng cỡ vạch theo bước nhãn: {bước nhãn: (bước vạch nhỏ, bước vạch nổi)} (°C)
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
    # Bố cục: [nhãn trái | thanh màu | nhãn phải]
    # - Vạch chia nhỏ vẽ 2 bên thanh; nhãn ghi theo 'buoc' (°C).
    #   buoc=None (thang tự động): tự chọn bước nhãn/vạch theo độ rộng của thang
    #   để các vạch luôn dày vừa đủ, bất kể thang rộng hay hẹp.
    # - Nếu 2 nhãn liên tiếp quá sát nhau thì ghi ZIC ZAC: nhãn xen kẽ
    #   phải/trái để không đè lên nhau.
    # - Vạch/nhãn "nổi" (dài hơn, nhãn màu vàng) đánh dấu các mốc chính.
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = 0.40

    w = w_lb + w_bar + w_lb
    x0 = w_lb            # mép trái thanh màu
    x1 = w_lb + w_bar    # mép phải thanh màu
    nen = np.full((cao, w, 3), 25, dtype=np.uint8)

    h_bar = max(1, cao - 2 * pad)

    grad = np.linspace(255, 0, h_bar).astype(np.uint8)
    grad = np.repeat(grad.reshape(-1, 1), w_bar, axis=1)
    bar = cv2.applyColorMap(grad, cv2.COLORMAP_TURBO)

    nen[pad:pad + h_bar, x0:x1] = bar

    cv2.rectangle(nen, (x0, pad), (x1 - 1, pad + h_bar - 1), (210, 210, 210), 1)

    khoang = float(tmax - tmin)
    px_moi_do = h_bar / khoang

    # Chọn bước nhãn: cố định theo 'buoc', hoặc tự chọn bước nhỏ nhất mà 2 nhãn
    # liên tiếp cách nhau >= ~11 px (dưới 18 px sẽ ghi zic zac).
    if buoc is None:
        buoc = 20.0
        for b in sorted(BANG_VACH):
            if px_moi_do * b >= 11.0:
                buoc = b
                break

    sub, noi = BANG_VACH.get(float(buoc), (buoc / 2.0, buoc * 5.0))
    kl = max(1, int(round(buoc / sub)))   # số vạch nhỏ giữa 2 nhãn
    kn = max(1, int(round(noi / sub)))    # số vạch nhỏ giữa 2 vạch nổi
    zigzag = (px_moi_do * buoc) < 18.0
    co_le = buoc < 1.0

    k0 = int(np.ceil(tmin / sub - 1e-9))
    k1 = int(np.floor(tmax / sub + 1e-9))

    n_nhan = 0  # đếm số nhãn đã ghi, để xen kẽ trái/phải
    for k in range(k0, k1 + 1):
        t = k * sub
        ty = (t - tmin) / khoang
        y = int(pad + h_bar - ty * h_bar)
        y = max(pad, min(pad + h_bar - 1, y))

        chinh = (k % kl == 0)
        muc = (k % kn == 0)
        ldai = 12 if muc else (8 if chinh else 4)
        dday = 2 if muc else 1

        # Vạch chia ở cả 2 bên thanh màu
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

try:
    while True:

        with khoa:
            tho = anh_moi.copy()

        tho = tho.astype(np.float32)

        if du_lieu_muot is None:
            du_lieu_muot = tho.copy()
        else:
            du_lieu_muot = du_lieu_muot * (1.0 - ALPHA) + tho * ALPHA

        du_lieu_loc = cv2.GaussianBlur(du_lieu_muot, (3, 3), 0)

        cmin = float(du_lieu_loc.min())
        cmax = float(du_lieu_loc.max())
        ctb = float(du_lieu_loc.mean())

        big = cv2.resize(du_lieu_loc, (RONG * TYLE, CAO * TYLE), interpolation=cv2.INTER_CUBIC)
        big = cv2.GaussianBlur(big, (5, 5), 0)

        if thang_co_dinh:
            dmin, dmax = TMIN, TMAX
        else:
            dmin = float(np.percentile(du_lieu_loc, 5))
            dmax = float(np.percentile(du_lieu_loc, 95))
            if dmax - dmin < 4.0:
                tt = (dmin + dmax) / 2.0
                dmin = tt - 2.0
                dmax = tt + 2.0

        norm = np.clip((big - dmin) / (dmax - dmin), 0, 1)
        img8 = (norm * 255).astype(np.uint8)
        img8 = xu_ly_xam(img8)

        mau = cv2.applyColorMap(img8, cv2.COLORMAP_TURBO)

        mau, vmin, vmax = ve_max(mau, du_lieu_loc)

        ng_ht, ng_chuong_ht, bao = bao_chuong(vmax)  # ng_ht: ngưỡng cảnh báo; ng_chuong_ht: ngưỡng bật chuông

        cv2.rectangle(mau, (0, 0), (mau.shape[1] - 1, mau.shape[0] - 1), (255, 255, 255), 2, cv2.LINE_AA)

        hbar = mau.shape[0]
        bar = thanh_mau(hbar, dmin, dmax, BUOC if thang_co_dinh else None)

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

        # Báo cảnh báo MỚI cho app (có hysteresis + thời gian chờ tối thiểu để tránh spam).
        # Khi nhiệt độ tụt xuống dưới (ngưỡng - BIEN_DO_TAT_CANHBAO) thì thiết bị tự
        # ghi canhbao=false lên Firebase.
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

        # Đọc phím bấm để bật/tắt các chế độ xử lý ảnh
        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break
        elif key == ord('s'):
            sharp_on = not sharp_on
            print(f"[Che do] Lam net (Sharpen): {'BAT' if sharp_on else 'TAT'}")
        elif key == ord('f'):
            thang_co_dinh = not thang_co_dinh
            print(f"[Che do] Thang mau: {'CO DINH ' + str(TMIN) + '-' + str(TMAX) + ' C' if thang_co_dinh else 'TU DONG'}")

except KeyboardInterrupt:
    pass

finally:
    chay = False

    t_doc.join(timeout=2)
    t_fb.join(timeout=2)

    cv2.destroyAllWindows()
    GPIO.output(17, GPIO.LOW)
    GPIO.cleanup()

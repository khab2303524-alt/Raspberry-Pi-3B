// Dán đoạn code này vào Google Apps Script (script.google.com > New project)
// rồi Deploy > New deployment > Type: Web app
//   - Execute as: Me
//   - Who has access: Anyone (để Raspberry Pi gọi được mà không cần OAuth)
// Sau khi deploy, copy URL "Web app" và dán vào biến URL_APPS_SCRIPT trong file Python.

// Thay ID_THU_MUC_DRIVE bằng ID thư mục Drive muốn lưu ảnh
// (lấy từ URL thư mục: drive.google.com/drive/folders/<ID_NAY>)
var ID_THU_MUC_DRIVE = "1PiY4RS-Z2bZre8uCUQk3KXZpBdKsHe6d";

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);

    var tenFile = body.filename || ("frame_" + Date.now() + ".jpg");
    var maxTemp = body.max_temp;
    var timestamp = body.timestamp;

    var folder = DriveApp.getFolderById(ID_THU_MUC_DRIVE);

    // Nếu đã có file cùng tên (cùng "ô" trong vòng lặp 1000 ảnh),
    // xoá ảnh cũ đó trước — đây chính là bước "đè lên ảnh cũ nhất".
    var fileCu = folder.getFilesByName(tenFile);
    while (fileCu.hasNext()) {
      var f = fileCu.next();
      f.setTrashed(true);
    }

    var bytes = Utilities.base64Decode(body.data);
    var blob = Utilities.newBlob(bytes, "image/jpeg", tenFile);

    var file = folder.createFile(blob);
    file.setDescription("max_temp: " + maxTemp + ", timestamp: " + timestamp);

    var ketQua = {
      status: "ok",
      fileId: file.getId(),
      fileUrl: file.getUrl()
    };

    return ContentService
      .createTextOutput(JSON.stringify(ketQua))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    var loi = {
      status: "error",
      message: err.toString()
    };

    return ContentService
      .createTextOutput(JSON.stringify(loi))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
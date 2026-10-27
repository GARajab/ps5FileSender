import sys
import os
import json
import time
import ftplib
from pathlib import Path
from dataclasses import dataclass

from PySide6.QtCore import Qt, QObject, Signal, QThread
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QMessageBox,
    QAbstractItemView, QFrame
)

APP_NAME = "PS5 File Sender"
REMOTE_DIR = "/data/homebrew"
CHUNK_SIZE = 1024 * 1024
MAX_RETRIES = 3


def settings_path() -> Path:
    root = Path(os.environ.get("APPDATA", str(Path.home())))
    folder = root / "PS5FileSender"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def load_settings() -> dict:
    try:
        with settings_path().open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"host": "", "port": "2121"}


def save_settings(host: str, port: str) -> None:
    path = settings_path()
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump({"host": host, "port": port}, f, indent=2)
    temp.replace(path)


@dataclass
class TransferItem:
    path: str
    size: int


class UploadWorker(QObject):
    progress = Signal(int, int, int)       # row, bytes sent, total bytes
    status = Signal(int, str)              # row, status
    log = Signal(str)
    finished = Signal()

    def __init__(self, host: str, port: int, files: list[TransferItem]):
        super().__init__()
        self.host = host
        self.port = port
        self.files = files
        self._cancelled = False
        self.ftp = None

    def cancel(self):
        self._cancelled = True
        try:
            if self.ftp:
                self.ftp.close()
        except Exception:
            pass

    def connect_ftp(self):
        ftp = ftplib.FTP()
        ftp.connect(self.host, self.port, timeout=15)
        # Many homebrew FTP servers accept anonymous FTP with a blank password.
        ftp.login("anonymous", "anonymous@")
        ftp.set_pasv(True)
        ftp.cwd(REMOTE_DIR)
        self.ftp = ftp
        return ftp

    def ensure_connection(self):
        try:
            if self.ftp is not None:
                self.ftp.voidcmd("NOOP")
                return self.ftp
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass
            self.ftp = None
        return self.connect_ftp()

    def run(self):
        try:
            for row, item in enumerate(self.files):
                if self._cancelled:
                    self.status.emit(row, "Cancelled")
                    continue

                path = Path(item.path)
                total = path.stat().st_size
                success = False
                last_error = ""

                for attempt in range(1, MAX_RETRIES + 1):
                    if self._cancelled:
                        break
                    try:
                        ftp = self.ensure_connection()
                        sent = 0

                        def callback(chunk: bytes):
                            nonlocal sent
                            if self._cancelled:
                                raise RuntimeError("Upload cancelled")
                            sent += len(chunk)
                            self.progress.emit(row, sent, total)

                        self.status.emit(row, f"Uploading (attempt {attempt}/{MAX_RETRIES})")
                        self.log.emit(f"Uploading {path.name} to {REMOTE_DIR} ...")

                        with path.open("rb") as file_obj:
                            # STOR replaces an existing remote file with the same name.
                            ftp.storbinary(f"STOR {path.name}", file_obj, blocksize=CHUNK_SIZE, callback=callback)

                        self.progress.emit(row, total, total)
                        self.status.emit(row, "Completed")
                        self.log.emit(f"Completed: {path.name}")
                        success = True
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        self.log.emit(f"Attempt {attempt} failed for {path.name}: {last_error}")
                        try:
                            if self.ftp:
                                self.ftp.close()
                        except Exception:
                            pass
                        self.ftp = None
                        if attempt < MAX_RETRIES and not self._cancelled:
                            self.status.emit(row, f"Retrying ({attempt}/{MAX_RETRIES})")
                            # Small bounded backoff; worker thread only.
                            time.sleep(attempt * 1.5)

                if not success:
                    if self._cancelled:
                        self.status.emit(row, "Cancelled")
                    else:
                        self.status.emit(row, "Failed")
                        self.log.emit(f"FAILED: {path.name}: {last_error}")
        finally:
            try:
                if self.ftp:
                    self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.finished.emit()


class DropTable(QTableWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["File", "Size", "Progress", "Status"])
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setVisible(False)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isfile(path):
                    paths.append(path)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 620)
        self.setMinimumSize(760, 500)
        self.worker_thread = None
        self.worker = None
        self.file_paths = []
        self.row_progress = {}

        settings = load_settings()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        title = QLabel("PS5 File Sender")
        title.setStyleSheet("font-size: 25px; font-weight: 700;")
        subtitle = QLabel("Send files from Windows to your PS5 over FTP")
        subtitle.setStyleSheet("color: #a8b0bd; font-size: 12px;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        connection = QFrame()
        connection.setObjectName("connection")
        connection.setStyleSheet(
            "#connection { border: 1px solid #343b48; border-radius: 10px; }"
        )
        conn_layout = QHBoxLayout(connection)
        conn_layout.setContentsMargins(14, 12, 14, 12)
        conn_layout.addWidget(QLabel("PS5 IP / Host"))
        self.host_edit = QLineEdit(settings.get("host", ""))
        self.host_edit.setPlaceholderText("e.g. 192.168.1.50")
        conn_layout.addWidget(self.host_edit, 2)
        conn_layout.addWidget(QLabel("Port"))
        self.port_edit = QLineEdit(str(settings.get("port", "2121")))
        self.port_edit.setMaximumWidth(100)
        self.port_edit.setPlaceholderText("2121")
        conn_layout.addWidget(self.port_edit)
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self.test_connection)
        conn_layout.addWidget(self.test_button)
        layout.addWidget(connection)

        destination = QLabel(f"Destination folder:  {REMOTE_DIR}")
        destination.setStyleSheet("font-weight: 600; color: #7dd3a7;")
        layout.addWidget(destination)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Choose Files…")
        self.add_button.clicked.connect(self.choose_files)
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("Clear List")
        self.clear_button.clicked.connect(self.clear_files)
        self.send_button = QPushButton("Send to PS5")
        self.send_button.setObjectName("send")
        self.send_button.clicked.connect(self.start_upload)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        actions.addWidget(self.send_button)
        layout.addLayout(actions)

        self.table = DropTable()
        self.table.files_dropped.connect(self.add_files)
        layout.addWidget(self.table, 1)

        self.log_label = QLabel("Activity")
        self.log_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.log_label)
        self.log_box = QLabel("Ready. Drag files into the table or choose files.")
        self.log_box.setWordWrap(True)
        self.log_box.setMinimumHeight(42)
        self.log_box.setMaximumHeight(76)
        self.log_box.setStyleSheet(
            "background: #171b22; border: 1px solid #343b48; "
            "border-radius: 8px; padding: 9px; color: #c5ccd6;"
        )
        layout.addWidget(self.log_box)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #101319; color: #edf1f7; }
            QLineEdit, QTableWidget {
                background: #171b22; border: 1px solid #343b48;
                border-radius: 6px; padding: 7px; color: #edf1f7;
            }
            QHeaderView::section {
                background: #202631; color: #dfe5ef; padding: 8px;
                border: none; border-bottom: 1px solid #343b48;
            }
            QPushButton {
                background: #252c38; border: 1px solid #3a4352;
                border-radius: 7px; padding: 9px 13px; font-weight: 600;
            }
            QPushButton:hover { background: #303a49; }
            QPushButton:disabled { color: #777f8c; background: #1a1e25; }
            QPushButton#send { background: #26764d; border-color: #26764d; }
            QPushButton#send:hover { background: #2e8b5b; }
            QTableWidget { gridline-color: #2d3440; }
            QTableWidget::item { padding: 5px; }
        """)

    def append_log(self, message: str):
        current = self.log_box.text()
        if current == "Ready. Drag files into the table or choose files.":
            current = ""
        lines = ([current] if current else []) + [message]
        self.log_box.setText("\n".join(lines[-3:]))

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files to send")
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list):
        existing = set(self.file_paths)
        for raw_path in paths:
            path = os.path.abspath(raw_path)
            if not os.path.isfile(path) or path in existing:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(os.path.basename(path)))
            self.table.setItem(row, 1, QTableWidgetItem(self.format_size(size)))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("%p%")
            self.table.setCellWidget(row, 2, bar)
            self.table.setItem(row, 3, QTableWidgetItem("Queued"))
            self.file_paths.append(path)
            existing.add(path)
        self.append_log(f"File list contains {len(self.file_paths)} file(s).")

    @staticmethod
    def format_size(size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def remove_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            return
        for row in rows:
            self.table.removeRow(row)
            del self.file_paths[row]
        self.append_log("Removed selected file(s).")

    def clear_files(self):
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, APP_NAME, "Wait until the current upload finishes.")
            return
        self.table.setRowCount(0)
        self.file_paths.clear()
        self.append_log("File list cleared.")

    def validate_connection(self):
        host = self.host_edit.text().strip()
        port_text = self.port_edit.text().strip()
        if not host:
            QMessageBox.warning(self, APP_NAME, "Enter the PS5 IP address or host name.")
            return None
        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, APP_NAME, "Port must be a number from 1 to 65535.")
            return None
        save_settings(host, str(port))
        return host, port

    def test_connection(self):
        details = self.validate_connection()
        if not details:
            return
        host, port = details
        self.test_button.setEnabled(False)
        self.append_log("Testing FTP connection…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=8)
            ftp.login("anonymous", "anonymous@")
            ftp.set_pasv(True)
            ftp.cwd(REMOTE_DIR)
            ftp.quit()
            QMessageBox.information(
                self, APP_NAME,
                f"FTP connection succeeded and {REMOTE_DIR} is accessible."
            )
            self.append_log("Connection test succeeded.")
        except Exception as exc:
            QMessageBox.warning(
                self, APP_NAME,
                "Could not connect or access the destination folder.\n\n"
                f"Details: {exc}\n\n"
                "Check that the PS5 FTP server is running, the IP/port are correct, "
                "anonymous login is accepted, and the folder exists and is writable."
            )
            self.append_log(f"Connection test failed: {exc}")
        finally:
            QApplication.restoreOverrideCursor()
            self.test_button.setEnabled(True)

    def start_upload(self):
        details = self.validate_connection()
        if not details:
            return
        if not self.file_paths:
            QMessageBox.information(self, APP_NAME, "Choose or drop at least one file first.")
            return
        if self.worker_thread and self.worker_thread.isRunning():
            return

        host, port = details
        items = []
        for path in self.file_paths:
            try:
                items.append(TransferItem(path, os.path.getsize(path)))
            except OSError as exc:
                self.append_log(f"Skipping unreadable file {path}: {exc}")

        self.worker_thread = QThread(self)
        self.worker = UploadWorker(host, port, items)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_progress)
        self.worker.status.connect(self.update_status)
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.upload_finished)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        for row in range(self.table.rowCount()):
            self.table.item(row, 3).setText("Queued")
            bar = self.table.cellWidget(row, 2)
            if bar:
                bar.setValue(0)

        self.set_busy(True)
        self.worker_thread.start()

    def update_progress(self, row: int, sent: int, total: int):
        if 0 <= row < self.table.rowCount():
            bar = self.table.cellWidget(row, 2)
            if bar:
                bar.setValue(100 if total == 0 else int(sent * 100 / total))

    def update_status(self, row: int, status: str):
        if 0 <= row < self.table.rowCount():
            self.table.item(row, 3).setText(status)

    def upload_finished(self):
        self.set_busy(False)
        self.append_log("Upload queue finished.")
        self.worker = None
        self.worker_thread = None

    def set_busy(self, busy: bool):
        for widget in (self.add_button, self.remove_button, self.clear_button,
                       self.send_button, self.test_button, self.host_edit, self.port_edit):
            widget.setEnabled(not busy)

    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            answer = QMessageBox.question(
                self, APP_NAME,
                "An upload is running. Close the app anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.worker:
                self.worker.cancel()
            self.worker_thread.quit()
            self.worker_thread.wait(2500)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

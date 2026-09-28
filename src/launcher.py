from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
                             QProgressBar, QPushButton, QApplication, QMainWindow,
                             QFileDialog, QInputDialog, QMessageBox)
from PyQt5.QtGui import QPixmap, QIcon

import minecraft_launcher_lib as mll
from minecraft_launcher_lib.utils import get_minecraft_directory, get_available_versions
from minecraft_launcher_lib.install import install_minecraft_version
from minecraft_launcher_lib.command import get_minecraft_command

from subprocess import call
from sys import argv, exit
from uuid import uuid1
import webbrowser
import psutil
import json
import os

# ---- Microsoft login settings (register your own app in the Azure portal) ----
CLIENT_ID = 'your-azure-client-id'
REDIRECT_URI = 'http://localhost:3000/login'

# Minecraft directory
minecraft_directory = get_minecraft_directory().replace('minecraft', 'dlwrtlauncher')
CONFIG_FILE = os.path.join(minecraft_directory, 'launcher_config.json')


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(minecraft_directory, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)


def resolve_java(path):
    """Accepts a JDK root, its bin folder, or the java executable. Returns the executable or None."""
    if not path:
        return None
    exe = 'java.exe' if os.name == 'nt' else 'java'
    candidates = [path, os.path.join(path, 'bin', exe), os.path.join(path, exe)]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


class LaunchThread(QThread):
    progress_update_signal = pyqtSignal(int, int, str)
    state_update_signal = pyqtSignal(bool)
    account_update_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.version_id = ''
        self.username = ''
        self.mem = '2'
        self.java_path = None
        self.account = None  # dict with refresh_token, or None for offline
        self.progress = 0
        self.progress_max = 0
        self.progress_label = ''

    def update_progress_label(self, value):
        self.progress_label = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def update_progress(self, value):
        self.progress = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def update_progress_max(self, value):
        self.progress_max = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def run(self):
        self.state_update_signal.emit(True)
        try:
            # Refresh Microsoft login
            if self.account:
                self.update_progress_label('Refreshing login...')
                data = mll.microsoft_account.complete_refresh(
                    CLIENT_ID, None, REDIRECT_URI, self.account['refresh_token'])
                self.account_update_signal.emit(data)
                options = {'username': data['name'], 'uuid': data['id'], 'token': data['access_token']}
            else:
                options = {'username': self.username or 'Player', 'uuid': str(uuid1()), 'token': ''}

            # Install Minecraft
            install_minecraft_version(
                versionid=self.version_id, minecraft_directory=minecraft_directory,
                callback={'setStatus': self.update_progress_label,
                          'setProgress': self.update_progress,
                          'setMax': self.update_progress_max})

            # Java + RAM
            if self.java_path:
                options['executablePath'] = self.java_path
            options['jvmArguments'] = [f'-Xmx{self.mem}G', f'-Xms{self.mem}G']

            call(get_minecraft_command(version=self.version_id,
                                       minecraft_directory=minecraft_directory, options=options))
        except Exception as e:
            self.error_signal.emit(str(e))
        self.state_update_signal.emit(False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()

        # Assets
        script_dir = os.path.dirname(os.path.abspath(__file__))
        resources_dir = os.path.join(script_dir, 'assets')

        # Window setup
        self.setWindowTitle('Dlwrty Launcher')
        self.adjustSize()

        # Title
        self.centralwidget = QWidget(self)
        self.logo = QLabel(self.centralwidget)
        self.logo.setPixmap(QPixmap(os.path.join(resources_dir, 'title.png')))
        self.logo.setFixedSize(600, 50)
        self.logo.setScaledContents(True)
        self.setWindowIcon(QIcon(os.path.join(resources_dir, 'minecraft.ico')))

        # Account row
        self.account_label = QLabel(self.centralwidget)
        self.login_button = QPushButton(self.centralwidget)
        self.login_button.clicked.connect(self.toggle_login)
        account_row = QHBoxLayout()
        account_row.addWidget(self.account_label, 1)
        account_row.addWidget(self.login_button)

        # Username (offline mode only)
        self.username = QLineEdit(self.centralwidget)
        self.username.setPlaceholderText('Username (offline mode)')
        self.username.setText(self.config.get('username', ''))

        # Java path row
        self.java_edit = QLineEdit(self.centralwidget)
        self.java_edit.setPlaceholderText('JDK folder (empty = bundled/PATH java)')
        self.java_edit.setText(self.config.get('java_path', ''))
        self.java_browse = QPushButton('Browse...', self.centralwidget)
        self.java_browse.clicked.connect(self.browse_java)
        java_row = QHBoxLayout()
        java_row.addWidget(self.java_edit, 1)
        java_row.addWidget(self.java_browse)

        # Version select
        self.version_select = QComboBox(self.centralwidget)
        for version in get_available_versions(minecraft_directory):
            self.version_select.addItem(version['id'])
        last = self.config.get('version')
        if last:
            self.version_select.setCurrentText(last)

        # RAM select
        mem = round(psutil.virtual_memory().total / 1024 ** 3)
        self.mem = QComboBox(self.centralwidget)
        for i in range(1, mem + 1):
            self.mem.addItem(str(i))
        self.mem.setCurrentText(str(self.config.get('mem', 2)))

        # Progress
        self.start_progress_label = QLabel(self.centralwidget)
        self.start_progress_label.setVisible(False)
        self.start_progress = QProgressBar(self.centralwidget)
        self.start_progress.setVisible(False)

        # Play button
        self.start_button = QPushButton('Play', self.centralwidget)
        self.start_button.clicked.connect(self.launch_game)

        # Layout
        layout = QVBoxLayout(self.centralwidget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(account_row)
        layout.addWidget(self.username)
        layout.addLayout(java_row)
        layout.addWidget(self.mem)
        layout.addWidget(self.version_select)
        layout.addWidget(self.start_progress_label)
        layout.addWidget(self.start_progress)
        layout.addWidget(self.start_button)

        # Launch thread
        self.launch_thread = LaunchThread()
        self.launch_thread.state_update_signal.connect(self.state_update)
        self.launch_thread.progress_update_signal.connect(self.update_progress)
        self.launch_thread.account_update_signal.connect(self.store_account)
        self.launch_thread.error_signal.connect(self.show_error)

        self.setCentralWidget(self.centralwidget)
        self.refresh_account_ui()

    # ---- Account ----
    def refresh_account_ui(self):
        acc = self.config.get('account')
        if acc:
            self.account_label.setText(f"Logged in as {acc['name']}")
            self.login_button.setText('Log out')
            self.username.setEnabled(False)
        else:
            self.account_label.setText('Not logged in (offline mode)')
            self.login_button.setText('Log in with Microsoft')
            self.username.setEnabled(True)

    def store_account(self, data):
        self.config['account'] = {'name': data['name'], 'id': data['id'],
                                  'refresh_token': data['refresh_token']}
        save_config(self.config)
        self.refresh_account_ui()

    def toggle_login(self):
        if self.config.get('account'):
            self.config.pop('account')
            save_config(self.config)
            self.refresh_account_ui()
            return
        try:
            url, state, verifier = mll.microsoft_account.get_secure_login_data(CLIENT_ID, REDIRECT_URI)
            webbrowser.open(url)
            redirected, ok = QInputDialog.getText(
                self, 'Microsoft login',
                'Log in in your browser, then paste the URL you were redirected to:')
            if not ok or not redirected.strip():
                return
            code = mll.microsoft_account.parse_auth_code_url(redirected.strip(), state)
            data = mll.microsoft_account.complete_login(CLIENT_ID, None, REDIRECT_URI, code, verifier)
            self.store_account(data)
        except Exception as e:
            self.show_error(f'Login failed: {e}')

    # ---- Java ----
    def browse_java(self):
        path = QFileDialog.getExistingDirectory(self, 'Select JDK folder', self.java_edit.text() or '')
        if path:
            self.java_edit.setText(path)

    # ---- UI updates ----
    def state_update(self, value):
        self.start_button.setDisabled(value)
        self.start_progress_label.setVisible(value)
        self.start_progress.setVisible(value)

    def update_progress(self, progress, max_progress, label):
        self.start_progress.setValue(progress)
        self.start_progress.setMaximum(max_progress)
        self.start_progress_label.setText(label)

    def show_error(self, msg):
        QMessageBox.critical(self, 'Error', msg)

    def launch_game(self):
        java_text = self.java_edit.text().strip()
        java_exe = resolve_java(java_text)
        if java_text and not java_exe:
            self.show_error('Could not find a java executable in that folder.')
            return

        # Remember settings
        self.config.update({'java_path': java_text, 'username': self.username.text(),
                            'version': self.version_select.currentText(),
                            'mem': int(self.mem.currentText())})
        save_config(self.config)

        t = self.launch_thread
        t.version_id = self.version_select.currentText()
        t.username = self.username.text()
        t.mem = self.mem.currentText()
        t.java_path = java_exe
        t.account = self.config.get('account')
        t.start()


if __name__ == '__main__':
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    app = QApplication(argv)
    window = MainWindow()
    window.show()
    exit(app.exec_())

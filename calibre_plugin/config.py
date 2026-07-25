"""Persistent settings + Preferences widget for the X-Ray Generator plugin."""
from calibre.utils.config import JSONConfig
from qt.core import QCheckBox, QComboBox, QFormLayout, QLineEdit, QWidget

prefs = JSONConfig("plugins/xray_generator")
prefs.defaults["api_key"] = ""
prefs.defaults["model"] = "gemini-3.5-flash"
prefs.defaults["language"] = "de"
prefs.defaults["detail_level"] = "normal"
prefs.defaults["use_thinking"] = False
prefs.defaults["max_workers"] = 3

_LANGUAGES = ["en", "de"]
_DETAIL_LEVELS = ["normal", "detailed"]


class ConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QFormLayout(self)
        self.setLayout(layout)

        self.api_key_edit = QLineEdit(prefs["api_key"], self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow(_("Gemini API key:"), self.api_key_edit)

        self.model_edit = QLineEdit(prefs["model"], self)
        layout.addRow(_("Model:"), self.model_edit)

        self.language_combo = QComboBox(self)
        self.language_combo.addItems(_LANGUAGES)
        self.language_combo.setCurrentText(prefs["language"])
        layout.addRow(_("Language:"), self.language_combo)

        self.detail_combo = QComboBox(self)
        self.detail_combo.addItems(_DETAIL_LEVELS)
        self.detail_combo.setCurrentText(prefs["detail_level"])
        layout.addRow(_("Detail level:"), self.detail_combo)

        self.use_thinking_check = QCheckBox(self)
        self.use_thinking_check.setChecked(prefs["use_thinking"])
        layout.addRow(_("Use thinking mode:"), self.use_thinking_check)

    def save_settings(self):
        prefs["api_key"] = self.api_key_edit.text()
        prefs["model"] = self.model_edit.text() or prefs.defaults["model"]
        prefs["language"] = self.language_combo.currentText()
        prefs["detail_level"] = self.detail_combo.currentText()
        prefs["use_thinking"] = self.use_thinking_check.isChecked()

"""Check actual Qt 5.10 attachment file filters without opening user files."""
import os
import sys
import tempfile
import time
from unittest.mock import patch

install = os.path.dirname(sys.executable)
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(install, 'platforms')
from PyQt5.QtCore import QCoreApplication, QDir, QEvent
from PyQt5.QtWidgets import QApplication, QFileDialog, QFileSystemModel
from PyQt5.QtTest import QTest
from plugin.AIAssistant.native import NativeAssistantController

QCoreApplication.setLibraryPaths([install])
application = QApplication.instance() or QApplication([])
application.setQuitOnLastWindowClosed(False)

class Server:
    origin = 'http://127.0.0.1:9'
    token = 'picker-test-only'

controller = NativeAssistantController(Server())
controller.view = object()
try:
    with tempfile.TemporaryDirectory(prefix='picker-', dir=os.path.join(os.getcwd(), '.runtime')) as folder:
        filenames = ('part.stl', 'UPPER.STL', 'part.obj', 'part.3mf',
                     'requirements.txt', 'requirements.docx', 'image.png', 'image.JPG')
        for filename in filenames:
            with open(os.path.join(folder, filename), 'wb') as stream:
                stream.write(b'isolated picker fixture')

        def inspect_picker(parent, title, initial_directory, filters, *args, **kwargs):
            dialog = QFileDialog(None)
            dialog.setOption(QFileDialog.DontUseNativeDialog, True)
            dialog.setFileMode(QFileDialog.ExistingFiles)
            dialog.setWindowTitle(title)
            dialog.setDirectory(folder)
            dialog.setNameFilter(filters)
            dialog.show()
            try:
                model = dialog.findChild(QFileSystemModel)
                assert model is not None
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    application.processEvents()
                    index = model.index(folder)
                    if model.rowCount(index) > 0:
                        break
                    QTest.qWait(20)
                patterns = model.nameFilters()
                print('Default Qt patterns:', ' '.join(patterns), flush=True)
                assert QDir.match(patterns, 'part.stl'), 'Default attachment filter hides STL'
                assert QDir.match(patterns, 'UPPER.STL'), 'Default attachment filter hides uppercase STL'
                assert all(QDir.match(patterns, name) for name in filenames), 'Default filter must show all supported attachments'
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    application.processEvents()
                    index = model.index(folder)
                    visible_names = set(model.fileName(model.index(row, 0, index))
                                        for row in range(model.rowCount(index)))
                    if set(filenames).issubset(visible_names):
                        break
                    QTest.qWait(20)
                assert set(filenames).issubset(visible_names), 'Supported files missing from actual picker rows'
                QTest.qWait(100)
                assert dialog.grab().save(os.path.join(os.getcwd(), '.runtime', 'attachment-picker-fixed.png'))
                groups = dialog.nameFilters()
                assert len(groups) == 4, 'Expected combined, model, document and image filters'
                for label, sample in (('Модели', 'part.stl'), ('Документы', 'requirements.txt'), ('Изображения', 'image.png')):
                    selected = next(item for item in groups if item.startswith(label))
                    dialog.selectNameFilter(selected)
                    assert QDir.match(model.nameFilters(), sample), label
                print('Qt attachment picker: STL/STL uppercase, OBJ, 3MF, DOCX, TXT, PNG/JPG visible; four groups work', flush=True)
                return [], dialog.selectedNameFilter()
            finally:
                dialog.hide()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

        with patch('plugin.AIAssistant.native.QFileDialog.getOpenFileNames', side_effect=inspect_picker):
            controller.chooseAttachments()
finally:
    controller.view = None
    controller.dispose()
    application.quit()
    application.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
print('HOST_ATTACHMENT_PICKER_SMOKE_PASSED')

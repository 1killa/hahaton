import QtQuick 2.1
import QtQuick.Controls 1.3
import QtQuick.Layouts 1.1
import QtQuick.Window 2.1

Window {
    id: root
    objectName: "assistantWindow"
    width: 1160; height: 780
    minimumWidth: 980; minimumHeight: 680
    visible: false
    modality: Qt.NonModal
    title: "STE Assistant — чат"
    color: "#edf1f6"
    property string page: "chat"
    property bool formsUpdating: false
    property bool settingsEdited: false
    property bool requirementsEdited: false
    property bool followChat: true
    property string formConversation: ""

    function statusText(value) {
        var names = {idle: "Готов к работе", running: "Помощник работает…", paused: "Ожидает продолжения",
                     failed: "Нужна проверка", cancelled: "Остановлено", interrupted: "Запуск прерван"};
        return names[value] || value || "Подключение…";
    }
    function timeText(value) {
        if (typeof value !== "number") return "—";
        return Math.floor(value / 3600) + " ч " + Math.floor((value % 3600) / 60) + " мин";
    }
    function loadSettings() {
        if (settingsEdited) return;
        formsUpdating = true;
        baseUrl.text = manager.settingsBaseUrl; modelName.text = manager.settingsModel;
        formsUpdating = false;
    }
    function loadRequirements() {
        if (requirementsEdited) return;
        formsUpdating = true;
        var req = manager.requirements || {};
        var custom = []; var rules = req.settings || [];
        noSupport.checked = false;
        for (var i = 0; i < rules.length; ++i) {
            if (rules[i].key === "support_enable" && rules[i].value === false) noSupport.checked = true;
            else custom.push(rules[i]);
        }
        reqSettings.text = JSON.stringify(custom, null, 2);
        reqMaterial.text = req.material_id || "";
        reqDimensions.checked = !!req.preserve_dimensions;
        reqNotes.text = req.notes || "";
        formsUpdating = false;
    }
    function editSettings() {
        if (formsUpdating) return;
        settingsEdited = true; manager.editSettings(baseUrl.text, modelName.text);
    }
    function editRequirements() {
        if (formsUpdating) return false;
        requirementsEdited = true;
        var rules;
        try { rules = JSON.parse(reqSettings.text || "[]"); }
        catch (error) { return manager.editRequirements(reqSettings.text, reqMaterial.text, reqDimensions.checked, reqNotes.text); }
        if (!(rules instanceof Array)) return manager.editRequirements(reqSettings.text, reqMaterial.text, reqDimensions.checked, reqNotes.text);
        if (noSupport.checked) rules.push({key: "support_enable", value: false, scope: "global", extruder: 0});
        return manager.editRequirements(JSON.stringify(rules), reqMaterial.text, reqDimensions.checked, reqNotes.text);
    }
    function sendDraft() {
        if (!manager.busy && !manager.commandPending && manager.uploadsPending === 0) manager.sendMessage();
    }
    function messageText(item) {
        var value = typeof item.content === "string" ? item.content : "";
        var files = item.attachments || [];
        for (var i = 0; i < files.length; ++i) value += (value ? "\n" : "") + "Файл: " + files[i].name;
        return value;
    }
    Connections {
        target: manager
        onDraftChanged: if (composer.text !== manager.draft) composer.text = manager.draft
        onSettingsChanged: root.loadSettings()
        onRequirementsChanged: root.loadRequirements()
        onStateChanged: {
            if (root.formConversation !== manager.conversationId) {
                root.formConversation = manager.conversationId;
                root.requirementsEdited = false;
                root.loadRequirements();
            }
        }
        onSettingsSaved: { root.settingsEdited = false; apiKey.text = ""; clearKey.checked = false; root.loadSettings(); }
        onRequirementsSaved: { root.requirementsEdited = false; root.loadRequirements(); }
    }
    Component.onCompleted: { root.loadSettings(); root.loadRequirements(); }
    onVisibleChanged: manager.windowVisibilityChanged(visible)
    onClosing: { close.accepted = false; root.hide(); }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 20; spacing: 14
        RowLayout {
            Layout.fillWidth: true
            Label { text: "STE Assistant"; font.pixelSize: 25; font.bold: true; color: "#18334d" }
            Label { text: "Подготовка печати"; font.pixelSize: 14; color: "#68798d"; Layout.fillWidth: true }
            Rectangle { width: 8; height: 8; radius: 4; color: manager.busy ? "#e4a13b" : "#359a78" }
            Label { text: root.statusText(manager.status); color: "#4a6077" }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 16
            Rectangle {
                Layout.preferredWidth: 190; Layout.fillHeight: true; color: "#e2e9f1"; radius: 8
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 10
                    Label { text: "ДИАЛОГИ"; color: "#65788d"; font.pixelSize: 11; font.bold: true }
                    Button { objectName: "newConversationButton"; text: "+ Новый диалог"; Layout.fillWidth: true; enabled: !manager.busy && !manager.commandPending && manager.uploadsPending === 0; onClicked: { manager.newConversation(); root.page = "chat"; } }
                    ListView {
                        id: historyList; objectName: "conversationList"
                        Layout.fillWidth: true; Layout.fillHeight: true; spacing: 6; clip: true
                        model: manager.conversations
                        delegate: Rectangle {
                            width: historyList.width; height: 54; radius: 5
                            color: modelData.id === manager.conversationId ? "#ffffff" : "#d8e2ed"
                            Text { anchors.fill: parent; anchors.margins: 9; text: modelData.title || "Новый диалог"; textFormat: Text.PlainText; color: "#2c435b"; wrapMode: Text.Wrap; elide: Text.ElideRight; maximumLineCount: 2; font.pixelSize: 12 }
                            MouseArea { anchors.fill: parent; enabled: !manager.busy && !manager.commandPending && manager.uploadsPending === 0; onClicked: { manager.selectConversation(modelData.id); root.page = "chat"; } }
                        }
                    }
                    Label { text: "Сцена общая для всех диалогов"; wrapMode: Text.Wrap; Layout.fillWidth: true; color: "#71849a"; font.pixelSize: 11 }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Button { text: "Чат"; checkable: true; checked: root.page === "chat"; onClicked: root.page = "chat" }
                    Button { text: "Сцена и файлы"; checkable: true; checked: root.page === "scene"; onClicked: root.page = "scene" }
                    Button { text: "Разработчикам"; checkable: true; checked: root.page === "developer"; onClicked: root.page = "developer" }
                    Item { Layout.fillWidth: true }
                    Button { text: "Условия"; checkable: true; checked: root.page === "requirements"; onClicked: root.page = "requirements" }
                    Button { text: "Настройки"; checkable: true; checked: root.page === "settings"; onClicked: root.page = "settings" }
                }
                Rectangle {
                    Layout.fillWidth: true; visible: manager.error.length > 0
                    implicitHeight: errorText.implicitHeight + 18; color: "#ffebeb"; radius: 5
                    Text { id: errorText; anchors.fill: parent; anchors.margins: 9; anchors.rightMargin: 42; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: manager.error; color: "#9b3030" }
                    Button { width: 28; text: "×"; anchors.right: parent.right; anchors.rightMargin: 5; anchors.verticalCenter: parent.verticalCenter; onClicked: manager.dismissError() }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true; color: "white"; radius: 8
                    Item {
                        anchors.fill: parent; anchors.margins: 16; visible: root.page === "chat"
                        ColumnLayout {
                            anchors.fill: parent; spacing: 10
                            Label { visible: !manager.hasKey; text: "Для отправки сообщений укажите API-ключ в настройках."; color: "#8c691d"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            ListView {
                                id: chatList; objectName: "chatMessages"
                                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12; clip: true
                                model: manager.messages
                                onCountChanged: if (root.followChat) positionViewAtEnd()
                                onMovementEnded: root.followChat = atYEnd
                                delegate: Rectangle {
                                    width: chatList.width - 18; x: modelData.role === "user" ? 18 : 0
                                    height: messageBody.implicitHeight + 48; color: modelData.role === "user" ? "#e7f0fc" : "#f1f4f8"; radius: 6
                                    Text { x: 12; y: 9; text: modelData.role === "user" ? "Вы" : "Помощник"; color: "#507293"; font.bold: true; font.pixelSize: 11 }
                                    TextEdit { id: messageBody; x: 12; y: 29; width: parent.width - 24; readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; font.pixelSize: 14; color: "#263b51"; text: root.messageText(modelData) }
                                }
                                Label { anchors.centerIn: parent; visible: chatList.count === 0; text: "Опишите деталь и задачу печати.\nМожно прикрепить модель и ТЗ."; horizontalAlignment: Text.AlignHCenter; color: "#7d8fa3"; font.pixelSize: 16 }
                            }
                            Flow {
                                Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 5
                                Repeater { model: manager.attachments; delegate: Button { text: (modelData.name || "Файл") + "  ×"; enabled: !manager.commandPending; onClicked: manager.removeAttachment(modelData.id) } }
                            }
                            Label { visible: manager.uploadsPending > 0; text: "Прикрепление файлов… " + manager.uploadsPending; color: "#687e96" }
                            RowLayout {
                                visible: manager.uploadError.length > 0; Layout.fillWidth: true
                                Label { text: manager.uploadError; color: "#a53737"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                Button { text: "Убрать ошибку"; onClicked: manager.clearUploadError() }
                            }
                            Label { text: "Сообщение"; color: "#61748a"; font.pixelSize: 12 }
                            TextArea {
                                id: composer; objectName: "chatComposer"
                                Layout.fillWidth: true; Layout.preferredHeight: 104
                                text: manager.draft; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; font.pixelSize: 14
                                onTextChanged: if (activeFocus) manager.setDraft(text)
                                Keys.onPressed: {
                                    if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !(event.modifiers & Qt.ShiftModifier)) {
                                        root.sendDraft(); event.accepted = true;
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Button { objectName: "attachButton"; text: "Прикрепить файл"; enabled: !manager.busy && !manager.commandPending && manager.uploadsPending === 0; onClicked: manager.chooseAttachments() }
                                Label { text: "Enter — отправить · Shift+Enter — новая строка"; font.pixelSize: 10; color: "#7a8da1"; Layout.fillWidth: true; elide: Text.ElideRight }
                                Button { objectName: "continueButton"; text: "Продолжить"; visible: manager.canContinue; enabled: !manager.commandPending; onClicked: manager.continuePending() }
                                Button { objectName: "cancelButton"; text: "Остановить"; enabled: manager.busy || manager.commandPending; onClicked: manager.cancel() }
                                Button { objectName: "sendButton"; text: manager.commandPending ? "Отправка…" : "Отправить"; enabled: !manager.busy && !manager.commandPending && manager.uploadsPending === 0 && (composer.text.trim().length > 0 || manager.attachments.length > 0); onClicked: root.sendDraft() }
                            }
                        }
                    }
                    Item {
                        anchors.fill: parent; anchors.margins: 18; visible: root.page === "scene"
                        ColumnLayout {
                            anchors.fill: parent; spacing: 12
                            Label { text: "Сцена и подготовка печати"; font.pixelSize: 19; color: "#28445f" }
                            Label { text: "Моделей: " + ((manager.scene.objects || []).length) + "     Режим: " + (manager.scene.mode || "—"); color: "#647b93" }
                            Repeater { model: manager.scene.objects || []; delegate: Label { text: (modelData.name || "Модель") + (modelData.bounds_mm ? " · " + modelData.bounds_mm.x + " × " + modelData.bounds_mm.y + " × " + modelData.bounds_mm.z + " мм" : ""); color: "#344f69"; Layout.fillWidth: true; elide: Text.ElideRight } }
                            Label { text: "Нарезка: " + (manager.sliceInfo.state || "нет данных"); color: "#526c86" }
                            ProgressBar { Layout.fillWidth: true; value: manager.sliceInfo.progress || 0; minimumValue: 0; maximumValue: 1 }
                            Label { text: "Время: " + root.timeText((manager.sliceInfo.result || {}).time_seconds) + "     Материал: " + (typeof (manager.sliceInfo.result || {}).material_g === "number" ? manager.sliceInfo.result.material_g.toFixed(1) + " г" : "—"); color: "#526c86" }
                            Flow {
                                Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 6
                                Button { text: "Обновить сцену"; enabled: !manager.busy; onClicked: manager.manualTool("get_scene", {}) }
                                Button { text: "Проверить 5D"; enabled: !manager.busy; onClicked: manager.manualTool("check_5d", {}) }
                                Button { text: "Нарезать"; enabled: !manager.busy; onClicked: manager.manualTool("start_slice", {}) }
                                Button { text: "Сохранить 3MF"; enabled: !manager.busy; onClicked: manager.manualTool("save_project", {filename: "project.3mf"}) }
                                Button { text: "Экспорт G-code"; enabled: !manager.busy; onClicked: manager.manualTool("export_gcode", {filename: "result.gcode"}) }
                            }
                            Label { objectName: "sceneActionMessage"; text: manager.actionMessage; visible: text.length > 0; color: "#355e74"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            Label { text: "Файлы результатов"; color: "#28445f"; font.pixelSize: 16 }
                            ListView {
                                id: outputList; objectName: "outputList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6; model: manager.outputs
                                delegate: Rectangle {
                                    width: outputList.width; height: 48; radius: 5; color: "#f1f5f9"
                                    Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 140; text: modelData.filename || "Файл"; elide: Text.ElideRight; textFormat: Text.PlainText; color: "#36516c" }
                                    Button { objectName: "saveOutput_" + modelData.id; anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; text: modelData.available === false ? "Недоступен" : "Сохранить…"; enabled: modelData.available !== false; onClicked: manager.saveOutput(modelData.id) }
                                }
                                Label { anchors.centerIn: parent; visible: outputList.count === 0; text: "После сохранения проекта или экспорта здесь появятся файлы."; color: "#8394a6"; wrapMode: Text.Wrap; width: parent.width - 20; horizontalAlignment: Text.AlignHCenter }
                            }
                        }
                    }
                    Item {
                        anchors.fill: parent; anchors.margins: 20; visible: root.page === "settings"
                        ColumnLayout {
                            objectName: "settingsForm"
                            enabled: !manager.commandPending
                            anchors.fill: parent; spacing: 12
                            Label { text: "Подключение к модели"; font.pixelSize: 19; color: "#28445f" }
                            Label { text: "Адрес API"; color: "#647b93" }
                            TextField { id: baseUrl; objectName: "providerUrl"; Layout.fillWidth: true; onTextChanged: if (activeFocus) root.editSettings() }
                            Label { text: "Модель"; color: "#647b93" }
                            TextField { id: modelName; objectName: "providerModel"; Layout.fillWidth: true; onTextChanged: if (activeFocus) root.editSettings() }
                            Label { text: manager.hasKey ? "API-ключ сохранён. Пустое поле сохраняет прежний ключ." : "API-ключ"; color: "#647b93"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            TextField { id: apiKey; objectName: "providerKey"; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: "Введите ключ" }
                            CheckBox { id: clearKey; text: "Удалить сохранённый ключ"; onCheckedChanged: manager.clearApiKey(checked) }
                            Button { objectName: "saveSettingsButton"; text: "Сохранить настройки"; enabled: !manager.busy && !manager.commandPending; onClicked: { root.editSettings(); manager.setApiKey(apiKey.text); manager.clearApiKey(clearKey.checked); manager.saveSettings(); } }
                            Item { Layout.fillHeight: true }
                        }
                    }
                    Item {
                        anchors.fill: parent; anchors.margins: 20; visible: root.page === "requirements"
                        ColumnLayout {
                            objectName: "requirementsForm"
                            enabled: !manager.commandPending
                            anchors.fill: parent; spacing: 10
                            Label { text: "Условия печати"; font.pixelSize: 19; color: "#28445f" }
                            Label { text: "Помощник проверяет обязательные условия перед изменениями."; color: "#647b93"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            RowLayout {
                                CheckBox { id: noSupport; text: "Без поддержек"; onCheckedChanged: if (!root.formsUpdating) root.editRequirements() }
                                CheckBox { id: reqDimensions; text: "Сохранять габариты"; onCheckedChanged: if (!root.formsUpdating) root.editRequirements() }
                            }
                            Label { text: "Материал (ID, если обязателен)"; color: "#647b93" }
                            TextField { id: reqMaterial; objectName: "requirementMaterial"; Layout.fillWidth: true; onTextChanged: if (activeFocus) root.editRequirements() }
                            Label { text: "Дополнительные обязательные настройки: JSON-массив ключей и значений"; color: "#647b93"; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            TextArea { id: reqSettings; objectName: "requirementSettings"; Layout.fillWidth: true; Layout.preferredHeight: 110; textFormat: TextEdit.PlainText; onTextChanged: if (activeFocus) root.editRequirements() }
                            Label { text: "Пожелания и пояснения"; color: "#647b93" }
                            TextArea { id: reqNotes; objectName: "requirementNotes"; Layout.fillWidth: true; Layout.preferredHeight: 85; textFormat: TextEdit.PlainText; onTextChanged: if (activeFocus) root.editRequirements() }
                            Button { objectName: "saveRequirementsButton"; text: "Сохранить условия"; enabled: !manager.busy && !manager.commandPending; onClicked: if (root.editRequirements()) manager.saveRequirements() }
                            Item { Layout.fillHeight: true }
                        }
                    }
                    Item {
                        anchors.fill: parent; anchors.margins: 18; visible: root.page === "developer"
                        ColumnLayout {
                            anchors.fill: parent; spacing: 10
                            Label { text: "Журнал разработчика"; font.pixelSize: 19; color: "#28445f" }
                            Label { text: "Технические события и вызовы инструментов"; color: "#647b93" }
                            ListView {
                                id: eventList; objectName: "developerEvents"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8; model: manager.events
                                delegate: Rectangle {
                                    width: eventList.width; height: eventText.implicitHeight + 20; color: "#f0f4f8"; radius: 5
                                    TextEdit { id: eventText; x: 10; y: 10; width: parent.width - 20; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText; text: JSON.stringify(modelData, null, 2); color: "#455f78"; font.family: "Consolas"; font.pixelSize: 12 }
                                }
                                Label { anchors.centerIn: parent; visible: eventList.count === 0; text: "Событий пока нет"; color: "#8394a6" }
                            }
                        }
                    }
                }
            }
        }
    }
}

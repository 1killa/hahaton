# Контракты черновика v2

Основание: `ПЛАН_ПРОЕКТА.md` в рабочей папке и задание организаторов. Python плагина совместим с 3.5, без внешних зависимостей. Проверенная установленная версия — STE Slicer 7.10.12. API плагинов — 5.

## Адаптер

`agent/slicer.py`: `SlicerAdapter(app, output_dir)` и `DemoAdapter(output_dir)`. Методы `list_tools()` (MCP tool schemas), `call(name, arguments)` (JSON-serializable dict), `snapshot_settings()` и `restore_settings(snapshot)`. Реальный адаптер создаётся и вызывается только в Qt main thread. Возвращает `{"ok": false, "error": "...", "code": "UNAVAILABLE"}` при недоступном API; исключения также перехватывает registry.

Имена и аргументы инструментов:

- `get_scene {}` → `{objects:[{id,name,bounds_mm:{x,y,z},position_mm:[x,y,z],scale:[x,y,z]}],mode,material_id}`.
- `get_settings {keys:[string],scope:"global"|"extruder",extruder:0}` → `{values:{key:value},scope}`.
- `set_setting {key,value,scope:"global"|"extruder",extruder:0}`.
- `set_print_mode {mode:"classic"|"cylindrical"|"cylindrical_full"}`.
- `list_profiles {}` → `{profiles:[{id,name,quality_type}],materials:[{id,name}]}`.
- `select_profile {quality_type}`; `select_material {material_id,extruder:0}`.
- `create_profile {name}`; `save_profile {}`.
- `load_model {path}`: только внутренний путь, подставленный registry из `upload_id`; агенту доступен только `upload_id`.
- `transform_model {object_id,action:"translate"|"rotate"|"scale",x,y,z}`: translate мм относительно текущего положения, rotate градусы относительно текущего, scale положительные множители относительно текущего. Координаты API STE: Y вверх, X/Z плоскость стола; это отражать в результатах.
- `arrange_models {}`; `remove_model {object_id}`.
- `start_slice {}` → `{state,plugin_generation_id}`; `get_slice_status {}` → `{state:"not_started"|"running"|"done"|"error"|"unavailable"|"stale",progress:0..1,result:{available:boolean,time_seconds?,material_mm?,material_g?},plugin_generation_id?,evidence?}`.
- `save_project {filename}`; `export_gcode {filename}` → `{ok:true,path,filename,size_bytes,evidence?}`. Только безопасное имя файла внутри output_dir. Для G-code обязательна новая завершённая generation; мутация инвалидирует результат.
- `check_5d {}` → `{facts:{...},warnings:[...],assumptions:[...],verified_geometry:false}`; никакого заявления о прочности/точной удалённой геометрии без проверки.

Snapshot сохраняет **наличие** и значения всех userChanges global/extruders; restore убирает новые overrides. Варианты меняют только настройки, не материалы, профили или transforms. Восстановление инвалидирует прежний G-code. Метрики только PrintInformation; неизвестные поля отсутствуют. Demo возвращает ошибки UNAVAILABLE для слайсинга/экспорта, никогда не создаёт fake G-code/3MF.

## HTTP для SPA

Статика `/`, `/app.js`, `/style.css`. Все `/api/*` требуют `X-AIAssistant-Token`. Токен передан fragment `#token=...`, извлекается и хранится в sessionStorage, fragment удаляется history.replaceState. Auth не передаётся query/body. Все POST имеют `Content-Type: application/json` и точный Origin; fetch создаёт Origin автоматически. Ошибки JSON `{error:string}` и HTTP non-2xx. POST ожидают объект.

`GET /api/state?after=<event_id>`:

```json
{"mode":"demo","busy":false,"configured":false,"settings":{"base_url":"https://api.timeweb.ai/v1","model":"dashscope/qwen3.8-flash","has_key":false},"messages":[],"events":[],"cursor":0,"scene":{"objects":[]},"slice":{"state":"unavailable","progress":0,"result":{"available":false}},"requirements":{"settings":[],"material_id":"","preserve_dimensions":false,"notes":""},"variants":[],"outputs":[],"last_error":""}
```

Сообщения `{role:"user"|"assistant",content:string,attachments?:[{name,kind}]}`. События `{id,type:"info"|"error"|"tool_start"|"tool_result",name?,arguments?,result?,message?}`. Для текста использовать textContent. State snapshot — авторитет для истории; события дополняются по id.

- `POST /api/settings {base_url,model,api_key:"",clear_key:false}`: пустой api_key сохраняет прежний. Ответ masked settings. В demo key только в памяти процесса; в плагине STE preferences. Никогда не класть key в local/sessionStorage.
- `POST /api/upload {filename,data_base64}` → `{id,name,kind:"image"|"model",mime_type}`. До 24 MiB decoded на файл; расширения PNG/JPG/JPEG/STL/OBJ/3MF. В сообщении передавать IDs. Изображения идут в LLM как base64 image_url; модель загружается через инструмент с upload_id.
- `POST /api/message {text,attachments:[id]}` → HTTP 202 `{accepted:true}`. Enter отправляет, Shift+Enter новая строка.
- `POST /api/cancel {}`; `POST /api/reset {}` — reset только когда idle.
- `POST /api/requirements {settings:[{key,value,scope:"global"|"extruder",extruder:0}],material_id:"",preserve_dimensions:false,notes:""}`. Это полная замена. Отсутствующие scope/extruder нормализуются в явно сохранённые global/0. Settings/material/dimensions — жёсткие; notes — рекомендации, которые нельзя считать автоматически проверенными. В UI можно дать key/value fields, checkbox no supports → support_enable=false и preserve dimensions.
- `POST /api/tool {name,arguments:{...}}` → `{result:...}`. Доступен idle. Для ручных кнопок `get_scene`, `check_5d`, `start_slice`, `save_project {filename:"project.3mf"}`, `export_gcode {filename:"result.gcode"}`, `apply_variant {variant_id}`. Долгие операции отвечают 202 и завершаются событиями/state.
- `compare_variants {variants:[{label,settings:{layer_height:0.2,infill_sparse_density:20}}]}` — registry tool, 2–3 настоящих слайсинга. Demo отказывает явно. UI запускает запросом чата или manual tool.
- Outputs `{id,filename,size_bytes,url}`; скачивание через `fetch(output.url,{headers:auth})` и Blob, чтобы auth оставался в заголовке. `GET /api/download/<id>`.
- Variants `{id,label,settings,result,outputs}`; применение `apply_variant` инвалидирует slice, дальше требуется новый слайсинг.

## Обнаружение API установленного STE

Подтверждено чтением встроенных плагинов и десериализацией code objects штатным python35.dll, без изменения приложения:

| Возможность | Точный API / источник | Статус до live smoke |
|---|---|---|
| Main thread | app.callLater(func,*args) | Требование организаторов; runtime bridge проверить |
| Backend | app.getBackend() → backend_manager.getCurrentBackend() | Сигнатура подтверждена bytecode |
| Материал | MachineManager.setMaterialById(self,position,root_material_id) | Подтверждено bytecode |
| Качество | setQualityGroupByQualityType(self,quality_type) | Подтверждено bytecode |
| Профили | QualityManager.getQualityGroups(self,machine), createQualityChanges(self,base_name) | Подтверждено bytecode |
| Translation | TranslateOperation(node,translation,set_position=False) | Подтверждено bytecode |
| Rotation | RotateOperation(node,rotation,rotate_around_point=None) | Подтверждено bytecode |
| Scale | ScaleOperation(node,scale,set_scale,add_scale,relative_scale,scale_around_point,snap) | Имена аргументов подтверждены; defaults читать при реализации |
| 3MF проекта | app.getWorkspaceFileHandler().getWriter("3MFWriter").write(stream,nodes) | ThreeMFWorkspaceWriter.py, BinaryMode; не mesh-only writer |
| G-code | app.getMeshFileHandler().getWriter("GCodeWriter").write(stream,nodes) | GCodeWriter.py; scene.gcode_dict[activeBuildPlate] |
| Slice | backend.forceSlice(); backendStateChange, processingProgress, slicingStarted | Чтение исходников native backends |
| PrintInformation | currentPrintTime.getDisplayString(DurationFormat.Seconds), materialLengths, materialWeights | QML + bytecode; отсутствующие поля не выдумывать |

Импорт полного MachineManager вне приложения выявил циклические импорты; это не подтверждение поломки плагина. Для фактической регистрации нужен отдельный smoke внутри STE.

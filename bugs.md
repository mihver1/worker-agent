# План исправления багов TUI

## Приоритеты

### P0 — сначала
1. Multiline ввод / multiline paste в TUI
2. Копирование текста ответа агента
3. Нормальная работа copy/paste в рамках TUI UX

### P1 — затем
4. Хоткеи на кириллической раскладке
5. Вставка скриншотов / изображений

### P2 — отдельно
6. Неописанный баг из пункта `6.` — нужен отдельный сценарий и критерии

---

## Что уже видно по коду

### Основные места
- `packages/artel-tui/src/artel_tui/app.py`
  - основной ввод сейчас сделан через `Input`, а не через multiline-виджет
  - сообщения агента рендерятся через `MessageWidget(Static)` + `Markdown/Text`
- `tests/test_tui_phase5.py`
  - тут уже есть покрытие для фокуса инпута, автокомплита и части TUI поведения
- `packages/artel-ai/src/artel_ai/models.py`
  - сообщения пока текстовые, без полноценной модели вложений / изображений

Это значит:
- баг с multiline почти наверняка связан с тем, что используется single-line `Input`
- баг с копированием ответа агента связан с тем, что сообщения не являются selectable/editable text surface
- баг со скриншотами не только TUI-баг, а еще и backend/model-format gap

---

# 1. Не могу скопировать текст агента, выделение даже не работает

## Гипотеза
Сейчас ответы агента показываются как `Static`-виджеты с markdown/rendered text. У них нет нормального режима выделения текста, фокуса по сообщениям и явного action на copy.

## Пошаговый план
1. Зафиксировать ожидаемый UX:
   - что именно копируем: текущее сообщение, последний ответ агента, выделенный блок или весь ответ
   - нужен ли copy через hotkey, slash-команду и кнопку
2. Добавить регрессионные тесты в `tests/test_tui_phase5.py`:
   - copy последнего assistant message
   - copy длинного markdown ответа
   - copy code block без потери форматирования
3. В `packages/artel-tui/src/artel_tui/app.py` добавить модель “active/focused message”:
   - хранить id/индекс текущего сообщения
   - дать навигацию по сообщениям
4. Добавить action для копирования текста сообщения в clipboard:
   - использовать `App.copy_to_clipboard(...)`
   - добавить fallback-команду `/copy`
5. Добавить удобный режим просмотра сообщения:
   - открыть выбранный ответ в read-only `TextArea`/modal
   - там уже можно выделять текст нативно внутри TUI
6. Проверить поведение для:
   - markdown
   - многострочных code block
   - очень длинных ответов
   - remote/local mode
7. Обновить help/footer/README с новым способом копирования.

## Критерий готовности
- текст ответа можно скопировать без мыши
- длинный markdown и code blocks копируются целиком
- есть понятный fallback, если terminal selection недоступен

---

# 2. Не работают хоткеи на кириллической раскладке

## Гипотеза
Часть хоткеев завязана на буквенные key names (`ctrl+l`, `ctrl+o`, и т.д.), а при кириллической раскладке терминал/фреймворк может отдавать другие key values.

## Пошаговый план
1. Составить список всех текущих хоткеев:
   - из `ArtelApp.BINDINGS`
   - из кастомных keybindings в config
   - из ручной обработки `on_key`
2. Разделить хоткеи на 2 класса:
   - app-handled (можем чинить внутри Artel)
   - terminal/cmux-handled (нужны обходные пути и документация)
3. Ввести слой нормализации клавиш:
   - маппинг кириллических символов на латинские action aliases
   - отдельный helper для layout-insensitive shortcuts, где это возможно
4. Для важных действий добавить layout-safe альтернативы:
   - не только буквенные комбинации
   - slash-команды как fallback
   - при необходимости функциональные клавиши / `ctrl+shift+...`
5. Добавить тесты:
   - на алиасы hotkeys
   - на то, что ключевые действия доступны не только через латиницу
6. Обновить подсказки в UI:
   - если хоткей недоступен в данной среде, показать альтернативу (`/clear`, `/quit`, `/copy` и т.д.)

## Критерий готовности
- основные действия работают на кириллической раскладке
- для спорных terminal-level shortcut есть стабильный fallback внутри приложения

---

# 3. Дефолтный cmd+c/cmd+v не работают нормально

## Важное замечание
Для TUI в терминале `cmd+c/cmd+v` часто обрабатываются не приложением, а самим terminal emulator / cmux. Поэтому тут нужно разделить:
- что реально можно исправить в коде Artel
- что нужно обойти через clipboard actions / OSC52 / альтернативные бинды

## Пошаговый план
1. Зафиксировать матрицу поведения:
   - macOS Terminal / iTerm / Warp
   - внутри cmux и вне cmux
   - local TUI и remote TUI
2. Определить целевое поведение отдельно для:
   - ввода пользователя
   - копирования ответа агента
   - вставки простого текста
   - вставки multiline текста
3. Добавить app-level clipboard actions:
   - `copy current message`
   - `paste into composer`
   - slash-команды `/copy` и при необходимости `/paste`
4. Проверить и при необходимости включить/улучшить поддержку:
   - bracketed paste
   - OSC52 clipboard copy
   - fallback через modal/viewer
5. Пересмотреть конфликтующие бинды:
   - сейчас `ctrl+c` используется как quit
   - убедиться, что copy UX не конфликтует с quit UX
6. Добавить тесты для app-level сценариев:
   - paste в composer
   - copy ответа агента
   - отсутствие регрессии в quit/clear behavior
7. Добавить короткую документацию:
   - какие сочетания работают как terminal-native
   - какие сочетания гарантированы самим приложением

## Критерий готовности
- текстовая вставка и копирование имеют рабочий путь внутри приложения
- поведение не зависит полностью от особенностей терминала
- пользователь видит понятный fallback

---

# 4. Вставка скриншотов не работает в TUI

## Гипотеза
Это не только проблема UI. Сейчас модель сообщений в основном текстовая, а полноценной цепочки `clipboard image -> attachment -> provider payload` нет.

## Пошаговый план
1. Определить MVP:
   - вариант A: вставка изображения как attachment
   - вариант B: сначала сохранять изображение в файл и прикладывать как path/reference
2. Расширить доменную модель сообщений в `packages/artel-ai/src/artel_ai/models.py`:
   - добавить attachments / content parts
   - не ограничиваться только `content: str`
3. Обновить provider adapters:
   - Anthropic
   - OpenAI-compatible
   - Google
   - другие vision-capable провайдеры
4. Добавить TUI-пайплайн вставки:
   - определить, что из clipboard пришло изображение
   - сохранить временный PNG/JPEG в `.artel` temp area или системный temp
   - показать attachment chip / preview label в composer
5. Добавить валидацию:
   - если модель не vision-capable, показать понятную ошибку
   - если remote mode не поддерживает передачу attachment, показать fallback
6. Добавить тесты:
   - unit на message/attachment serialization
   - provider tests на image payload
   - TUI tests на появление attachment в composer
7. Обновить UX:
   - команда удаления attachment
   - индикация размера/типа файла

## Критерий готовности
- screenshot paste создает attachment
- attachment доезжает до vision-capable моделей
- для неподдерживаемых моделей пользователь получает понятную ошибку

---

# 5. Multiline вставка не работает в TUI
#    (и вручную multiline ввод тоже не работает)

## Гипотеза
Основной composer сейчас построен на `Input`, который однострочный. Это базовая архитектурная причина бага.

## Пошаговый план
1. Сначала добавить failing tests в `tests/test_tui_phase5.py`:
   - multiline paste сохраняет переносы строк
   - ручной ввод позволяет вставить newline
   - отправка multiline сообщения работает корректно
2. Заменить основной `#input-bar` в `packages/artel-tui/src/artel_tui/app.py`:
   - с `Input(...)`
   - на `TextArea` или отдельный multiline composer widget
3. Переопределить UX отправки:
   - `Enter` -> newline
   - `Ctrl+Enter` / `Alt+Enter` / настраиваемый shortcut -> send
   - placeholder и help нужно обновить
4. Адаптировать текущую логику команд:
   - slash-команды должны работать, если первая строка начинается с `/`
   - `!` / `!!` shell semantics не должны ломаться
   - автокомплит команд должен смотреть на текущий ввод корректно
5. Нормализовать paste behavior:
   - сохранять переносы строк
   - не схлопывать indentation
   - корректно обрабатывать большой pasted block
6. Проверить интеграции:
   - local mode
   - remote mode
   - permission panel -> возврат фокуса в новый composer
7. Обновить существующие тесты, где сейчас ожидается `Input`, на новый composer widget.

## Критерий готовности
- можно вручную вводить несколько строк
- можно вставлять многострочный текст без потери форматирования
- multiline сообщение корректно отправляется в агент

---

# 6. Пустой пункт

## Что нужно сделать
1. Описать баг одним предложением.
2. Добавить шаги воспроизведения.
3. Указать ожидаемое и фактическое поведение.
4. После этого включить в план как отдельный раздел.

---

# Рекомендуемый порядок реализации

## Этап 1 — база для ввода
1. Перевести основной composer с `Input` на multiline widget.
2. Починить multiline paste.
3. Обновить submit UX.

## Этап 2 — copy/select UX
4. Сделать copy текущего ответа агента.
5. Добавить modal/read-only viewer для выделения текста.
6. Добавить app-level clipboard actions и fallback команды.

## Этап 3 — hotkeys и раскладки
7. Ввести layout-safe key normalization.
8. Добавить альтернативные shortcut/fallback UX.

## Этап 4 — изображения
9. Спроектировать attachment model.
10. Поддержать screenshot paste end-to-end.

---

# Минимальный набор тестов перед merge

1. `multiline composer accepts newlines`
2. `multiline paste preserves line breaks`
3. `copy current assistant message writes full text to clipboard`
4. `copy code block preserves formatting`
5. `critical hotkeys have Cyrillic-layout fallback`
6. `image attachment is created from pasted screenshot` (когда начнется реализация)
7. `vision-unsupported model shows clear error for pasted image`

---

# Короткий итог

Самый выгодный путь:
1. сначала заменить single-line composer на multiline
2. затем добавить нормальный copy/select UX для ответов агента
3. потом закрыть раскладки и terminal-specific clipboard edge cases
4. в конце делать screenshot paste как отдельную multimodal фичу

const STORAGE_KEY = "pythonIdeState";
const defaultFiles = {
  "main.py": ``
};

const loadState = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

const savedState = loadState();
const files = savedState?.files && Object.keys(savedState.files).length > 0 ? savedState.files : defaultFiles;
let fileOrder = savedState?.fileOrder?.length ? savedState.fileOrder : Object.keys(files);
let activeFile = files[savedState?.activeFile] ? savedState.activeFile : fileOrder[0];

const fileList = document.getElementById("fileList");
const tabs = document.getElementById("tabs");
const editorContainer = document.getElementById("editor");
const outputArea = document.getElementById("outputArea");
const stdinInput = document.getElementById("stdinInput");
const useInputToggle = document.getElementById("useInputToggle");
const runButton = document.getElementById("runButton");
const clearButton = document.getElementById("clearButton");
const saveButton = document.getElementById("saveButton");
const newFileButton = document.getElementById("newFileButton");
const renameFileButton = document.getElementById("renameFileButton");
const deleteFileButton = document.getElementById("deleteFileButton");
const status = document.getElementById("status");
const cursorInfo = document.getElementById("cursorInfo");
const fileInfo = document.getElementById("fileInfo");
const statusRight = document.getElementById("statusRight");
const splitter = document.getElementById("splitter");
const workspace = document.querySelector(".workspace");
const themeToggle = document.getElementById("themeToggle");

let pyodideInstance = null;
let editor = null;
let stderrBuffer = "";
const models = new Map();
let completionRegistered = false;
let outputRatio = savedState?.outputRatio ?? null;
let theme = savedState?.theme === "light" ? "light" : "dark";
const GEMINI_API_KEY = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"; // ВСТАВЬТЕ СЮДА ВАШ КЛЮЧ
const GEMINI_MODEL = "gemini-2.5-flash";
let aiBusy = false;
let lastPastedImage = null;
let lastPastedImageTime = 0;

// Hacker Typing State
let codeBuffer = "";
let codeIndex = 0;
let isTypingMode = false;
let isEditMode = false;

const persistState = () => {
  const payload = {
    files,
    fileOrder,
    activeFile,
    stdin: stdinInput.value,
    useInput: useInputToggle.checked,
    outputRatio,
    theme,
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
};

const getApiKey = () => {
  if (GEMINI_API_KEY && GEMINI_API_KEY !== "YOUR_GEMINI_API_KEY") {
    return GEMINI_API_KEY;
  }
  appendOutput(
    "Не указан корректный ключ Gemini в коде (app.js). Найдите константу GEMINI_API_KEY и вставьте свой ключ.\\n",
    true
  );
  return null;
};

const getModelName = () => {
  return GEMINI_MODEL;
};

const blobToDataUrl = (blob) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Clipboard read failed"));
    reader.readAsDataURL(blob);
  });

const readClipboardImage = async () => {
  // Use passively captured image if it's "fresh" (within last 30 seconds)
  if (lastPastedImage && (Date.now() - lastPastedImageTime < 30000)) {
    const img = lastPastedImage;
    lastPastedImage = null;
    statusRight.textContent = "Ready";
    statusRight.style.color = "";
    return img;
  }

  // Fallback to active read if passive failed or is stale
  if (!navigator.clipboard || !navigator.clipboard.read) {
    return null;
  }
  try {
    const items = await navigator.clipboard.read();
    for (const item of items) {
      if (item.types.some(type => type.startsWith("image/"))) {
        const imageType = item.types.find(type => type.startsWith("image/"));
        const blob = await item.getType(imageType);
        return await blobToDataUrl(blob);
      }
    }
  } catch (e) {
    console.warn("Manual clipboard read failed, relying on paste event:", e);
  }
  return null;
};

const adjustForAutoindent = (code) => {
  const lines = code.split("\n");
  const result = [];
  let editorIndent = 0;

  for (const line of lines) {
    if (!line.trim()) {
      result.push("");
      continue;
    }

    const originalIndent = line.length - line.trimStart().length;
    const content = line.trimStart();

    if (originalIndent < editorIndent) {
      const outdentLevels = Math.floor((editorIndent - originalIndent) / 4);
      result.push("\b".repeat(outdentLevels) + content);
    } else {
      const extraSpaces = originalIndent - editorIndent;
      result.push(" ".repeat(extraSpaces) + content);
    }

    if (content.trimEnd().endsWith(":")) {
      editorIndent = originalIndent + 4;
    } else {
      editorIndent = originalIndent;
    }
  }

  return result.join("\n");
};

// Use capture phase (true) to catch the event before Monaco handles it
document.addEventListener("paste", async (event) => {
  const clipboardData = event.clipboardData || window.clipboardData;
  if (!clipboardData) return;

  const items = clipboardData.items;
  let imageFound = false;

  for (let i = 0; i < items.length; i++) {
    if (items[i].type.startsWith("image/")) {
      const blob = items[i].getAsFile();
      if (blob) {
        try {
          lastPastedImage = await blobToDataUrl(blob);
          lastPastedImageTime = Date.now();
          imageFound = true;
          statusRight.textContent = "🖼️ Image Ready";
          statusRight.style.color = "#4caf50";
          console.log("Image captured");
        } catch (e) {
          console.error("Paste capture failed:", e);
        }
      }
    }
  }

  if (!imageFound && clipboardData.files && clipboardData.files.length > 0) {
    for (const file of clipboardData.files) {
      if (file.type.startsWith("image/")) {
        lastPastedImage = await blobToDataUrl(file);
        lastPastedImageTime = Date.now();
        imageFound = true;
        statusRight.textContent = "🖼️ Image Ready";
        statusRight.style.color = "#4caf50";
      }
    }
  }
}, true);

const getEditorContext = () => {
  if (!editor) {
    return { context: "", line: 1, column: 1, selectionText: "" };
  }
  const model = editor.getModel();
  const position = editor.getPosition();
  const selection = editor.getSelection();
  if (!model || !position) {
    return { context: "", line: 1, column: 1, selectionText: "" };
  }
  const startLine = Math.max(1, position.lineNumber - 6);
  const endLine = Math.min(model.getLineCount(), position.lineNumber + 6);
  const range = new monaco.Range(startLine, 1, endLine, model.getLineMaxColumn(endLine));
  const context = model.getValueInRange(range);
  const selectionText =
    selection && !selection.isEmpty() ? model.getValueInRange(selection) : "";

  // Get text before cursor on the current line to help AI with corrections
  const lineContent = model.getLineContent(position.lineNumber);
  const lineContentBeforeCursor = lineContent.substring(0, position.column - 1);

  return {
    context,
    line: position.lineNumber,
    column: position.column,
    selectionText,
    lineContentBeforeCursor
  };
};

const extractOutputText = (data) => {
  if (!data) {
    return "";
  }
  // Gemini response format
  if (data.candidates && data.candidates[0] && data.candidates[0].content && data.candidates[0].content.parts) {
    // Filter for text parts
    return data.candidates[0].content.parts.map(p => p.text || "").join("");
  }
  return "";
};

const sanitizeInsertText = (text) => {
  if (!text) {
    return "";
  }
  let cleaned = text;

  // Remove Markdown code blocks if present
  if (cleaned.trim().startsWith("```")) {
    const lines = cleaned.split("\n");
    if (lines[0].trim().startsWith("```")) {
      lines.shift();
    }
    if (lines.length > 0 && lines[lines.length - 1].trim().startsWith("```")) {
      lines.pop();
    }
    cleaned = lines.join("\n");
  }

  return cleaned;
};

const requestAiAssist = async ({ code, context, line, column, selectionText, imageDataUrl, lineContentBeforeCursor }) => {
  const apiKey = getApiKey();
  if (!apiKey) {
    return "";
  }
  const model = getModelName();
  const systemInstruction =
    "You are an 8th-grade student participating in a programming olympiad.\n" +
    "Your rules:\n" +
    "1. Write very COMPACT and simple competitive-programming code. Solve the problem EXACTLY.\n" +
    "2. NO Object-Oriented Programming (no classes). NO imports.\n" +
    "3. Use One symbol or UZBEK ULTRAMINIMALISTIC variable(or function) names. Keep loop variables as 'i', 'j', 'n'.\n" +
    "4. Do NOT leave blank lines between logic blocks unless absolutely necessary. Keep the code vertically compact.\n" +
    "5. CRITICAL: DO NOT MANUALLY UNROLL LOOPS. If you need to check 5 elements, use a `for i in range(5):` loop. Never write 5 separate `if` statements for the same logic.\n" +
    "6. Return ONLY the code. No markdown code blocks (no ```python), no explanations, no comments.\n" +
    "7. DEDUCTION RULE: If you need to un-indent/go back one level (e.g., after a loop or an if-block ends) back to the left, output exactly ONE '\\x08' (backspace) character without spaces before it. DO NOT USE THIS RULE unless you need to break out of a block.";

  const userText = [
    `<context>`,
    `  <file name="${activeFile}">`,
    code,
    `  </file>`,
    `  <cursor line="${line}" column="${column}" />`,
    `  <line_prefix>${lineContentBeforeCursor}</line_prefix>`,
    `  <selection>${selectionText || ""}</selection>`,
    `  <surrounding_lines>`,
    context,
    `  </surrounding_lines>`,
    `</context>`,
    "Instruction: Generate the code starting from the beginning of the current line (matching <line_prefix> or correcting it).",
  ].join("\n");

  const contents = [{
    role: "user",
    parts: [{ text: userText }]
  }];

  if (imageDataUrl) {
    const base64Data = imageDataUrl.split(",")[1];
    const mimeType = imageDataUrl.split(";")[0].split(":")[1];
    contents[0].parts.push({
      inline_data: {
        mime_type: mimeType,
        data: base64Data
      }
    });
  }

  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      system_instruction: {
        parts: [{ text: systemInstruction }]
      },
      contents: contents,
      generationConfig: {
        temperature: 0.2, // Lower temperature for more deterministic code completion
      }
    }),
  });

  if (!response.ok) {
    const err = await response.text();
    appendOutput(`AI Error: ${err}\n`, true);
    return "";
  }
  const data = await response.json();
  return sanitizeInsertText(extractOutputText(data));
};
const handleAiHotkey = async (event) => {
  if (event.code !== "ShiftRight" || event.repeat) {
    return;
  }
  if (!editor || aiBusy) {
    return;
  }
  aiBusy = true;
  status.textContent = "Taking screenshot...";
  statusRight.textContent = "AI";
  try {
    const code = editor.getValue();
    const { context, line, column, selectionText, lineContentBeforeCursor } = getEditorContext();
    const imageDataUrl = await readClipboardImage();

    status.textContent = "AI thinking...";
    const rawAiCode = await requestAiAssist({
      code,
      context,
      line,
      column,
      selectionText,
      imageDataUrl,
      lineContentBeforeCursor,
    });

    if (!rawAiCode) {
      status.textContent = "AI returned nothing";
      return;
    }

    codeBuffer = adjustForAutoindent(rawAiCode);
    codeIndex = 0;
    isTypingMode = true;
    isEditMode = false;

    status.textContent = `Ready! ${codeBuffer.length} chars. Switch to editor, press ANY key.`;
    console.log("AI code ready for manual typing");
  } catch (e) {
    console.error("AI Assist failed:", e);
    status.textContent = "AI failed";
    statusRight.textContent = "Error";
  } finally {
    aiBusy = false;
  }
};

const handleKeyPress = async (event) => {
  // 1. ShiftRight triggers capture
  if (event.code === "ShiftRight" && !event.repeat) {
    handleAiHotkey(event);
    return;
  }

  // 2. Typing mode logic
  if (isTypingMode) {
    // F8 toggles Edit Mode
    if (event.code === "F8") {
      event.preventDefault();
      isEditMode = !isEditMode;
      status.textContent = isEditMode ? "Edit Mode: ON" : "Edit Mode: OFF (Code typing resumed)";
      return;
    }

    if (isEditMode) return;

    // ESC cancels
    if (event.code === "Escape") {
      isTypingMode = false;
      status.textContent = "Typing cancelled.";
      return;
    }

    // Backspace undo
    if (event.code === "Backspace") {
      event.preventDefault();
      if (codeIndex > 0) {
        codeIndex--;
        editor.trigger("ai", "deleteLeft", {});
      }
      return;
    }

    // Intercept ANY other key to type next char
    // Ignore modifier keys alone
    if (["Control", "Shift", "Alt", "Meta", "NumLock", "CapsLock"].includes(event.key)) {
      return;
    }

    event.preventDefault();
    if (codeIndex >= codeBuffer.length) {
      isTypingMode = false;
      status.textContent = "All code typed!";
      return;
    }

    const ch = codeBuffer[codeIndex];
    codeIndex++;

    if (ch === "\b") {
      editor.trigger("ai", "deleteLeft", {});
    } else {
      editor.trigger("ai", "type", { text: ch });
    }
  }
};

const languageFromFile = (name) => {
  if (name.toLowerCase().endsWith(".py")) {
    return "python";
  }
  return "plaintext";
};

const ensureModel = (name) => {
  if (models.has(name)) {
    return models.get(name);
  }
  const model = monaco.editor.createModel(files[name] ?? "", languageFromFile(name));
  model.onDidChangeContent(() => {
    files[name] = model.getValue();
    persistState();
  });
  models.set(name, model);
  return model;
};

const refreshFileList = () => {
  fileList.innerHTML = "";
  fileOrder.forEach((fileName) => {
    const item = document.createElement("div");
    item.className = `file-item${fileName === activeFile ? " active" : ""}`;
    item.textContent = fileName;
    item.setAttribute("draggable", "true");
    item.dataset.file = fileName;
    item.addEventListener("click", () => switchFile(fileName));
    item.addEventListener("dragstart", handleDragStart);
    fileList.appendChild(item);
  });
};

const refreshTabs = () => {
  tabs.innerHTML = "";
  fileOrder.forEach((fileName) => {
    const tab = document.createElement("div");
    tab.className = `tab${fileName === activeFile ? " active" : ""}`;
    tab.textContent = fileName;
    tab.setAttribute("draggable", "true");
    tab.dataset.file = fileName;
    tab.addEventListener("click", () => switchFile(fileName));
    tab.addEventListener("dragstart", handleDragStart);
    tabs.appendChild(tab);
  });
};

const updateCursorInfo = () => {
  if (!editor) {
    return;
  }
  const position = editor.getPosition();
  if (!position) {
    return;
  }
  cursorInfo.textContent = `Line ${position.lineNumber}, Column ${position.column}`;
};

const switchFile = (fileName) => {
  if (!editor) {
    return;
  }
  activeFile = fileName;
  const model = ensureModel(fileName);
  editor.setModel(model);
  fileInfo.textContent = fileName;
  refreshFileList();
  refreshTabs();
  updateCursorInfo();
  persistState();
};

const ensureUniqueName = (name) => {
  if (!files[name]) {
    return name;
  }
  const match = name.match(/^(.*?)(\.\w+)?$/);
  const base = match?.[1] || name;
  const ext = match?.[2] || "";
  let counter = 1;
  let candidate = `${base}_${counter}${ext}`;
  while (files[candidate]) {
    counter += 1;
    candidate = `${base}_${counter}${ext}`;
  }
  return candidate;
};

const createFile = (name, content = "") => {
  const safeName = ensureUniqueName(name.trim() || "untitled.py");
  files[safeName] = content;
  fileOrder = [...fileOrder, safeName];
  ensureModel(safeName);
  switchFile(safeName);
};

const renameFile = (oldName, newName) => {
  const trimmed = newName.trim();
  if (!trimmed || oldName === trimmed) {
    return;
  }
  const safeName = ensureUniqueName(trimmed);
  files[safeName] = files[oldName];
  delete files[oldName];
  fileOrder = fileOrder.map((name) => (name === oldName ? safeName : name));
  const model = models.get(oldName);
  if (model) {
    models.delete(oldName);
    models.set(safeName, model);
    monaco.editor.setModelLanguage(model, languageFromFile(safeName));
  }
  activeFile = safeName;
  fileInfo.textContent = safeName;
  refreshFileList();
  refreshTabs();
  persistState();
};

const deleteFile = (name) => {
  if (fileOrder.length === 1) {
    return;
  }
  const index = fileOrder.indexOf(name);
  const model = models.get(name);
  if (model) {
    model.dispose();
    models.delete(name);
  }
  delete files[name];
  fileOrder = fileOrder.filter((fileName) => fileName !== name);
  const nextFile = fileOrder[Math.max(0, index - 1)] || fileOrder[0];
  switchFile(nextFile);
};

const saveFile = () => {
  if (!editor) {
    return;
  }
  files[activeFile] = editor.getValue();
  const blob = new Blob([files[activeFile]], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = activeFile;
  link.click();
  URL.revokeObjectURL(url);
};

const handleDragStart = (event) => {
  const fileName = event.target?.dataset?.file;
  if (fileName) {
    event.dataTransfer.setData("text/plain", fileName);
    event.dataTransfer.effectAllowed = "move";
  }
};

const handleDropReorder = (event) => {
  event.preventDefault();
  const dragged = event.dataTransfer.getData("text/plain");
  const target = event.target?.dataset?.file;
  if (!dragged || !target || dragged === target) {
    return;
  }
  const draggedIndex = fileOrder.indexOf(dragged);
  const targetIndex = fileOrder.indexOf(target);
  if (draggedIndex === -1 || targetIndex === -1) {
    return;
  }
  const updated = [...fileOrder];
  updated.splice(draggedIndex, 1);
  updated.splice(targetIndex, 0, dragged);
  fileOrder = updated;
  refreshFileList();
  refreshTabs();
  persistState();
};

const handleExternalDrop = (event) => {
  const items = Array.from(event.dataTransfer?.files || []);
  if (items.length === 0) {
    return;
  }
  event.preventDefault();
  items.forEach((file) => {
    const reader = new FileReader();
    reader.onload = () => {
      createFile(file.name, String(reader.result || ""));
    };
    reader.readAsText(file);
  });
};

const appendOutput = (text, isError = false) => {
  const prefix = isError ? "Error: " : "";
  outputArea.textContent += `${prefix}${text}`;
  outputArea.scrollTop = outputArea.scrollHeight;
};

const clearOutput = () => {
  outputArea.textContent = "";
};

const buildRunner = (code, input, useInput) => {
  if (!useInput) {
    return code;
  }
  const escapedInput = JSON.stringify(input ?? "");
  return `import sys, io\nsys.stdin = io.StringIO(${escapedInput})\n${code}`;
};

const parseTracebackMarkers = (trace, model) => {
  if (!trace || !model) {
    return [];
  }
  const markers = [];
  const regex = /File "<exec>", line (\d+)/g;
  let match = regex.exec(trace);
  const lines = trace.trim().split("\n").filter(Boolean);
  const message = lines[lines.length - 1] || "Runtime error";
  while (match) {
    const lineNumber = Number(match[1]);
    markers.push({
      severity: monaco.MarkerSeverity.Error,
      message,
      startLineNumber: lineNumber,
      startColumn: 1,
      endLineNumber: lineNumber,
      endColumn: model.getLineMaxColumn(lineNumber),
    });
    match = regex.exec(trace);
  }
  return markers;
};

const setMarkers = (markers) => {
  if (!editor) {
    return;
  }
  const model = editor.getModel();
  if (!model) {
    return;
  }
  monaco.editor.setModelMarkers(model, "pyodide", markers);
};

const runCode = async () => {
  if (!editor) {
    return;
  }
  clearOutput();
  stderrBuffer = "";
  setMarkers([]);
  status.textContent = "Running...";
  statusRight.textContent = "Running";
  runButton.disabled = true;
  try {
    files[activeFile] = editor.getValue();
    if (!pyodideInstance) {
      pyodideInstance = await loadPyodide({
        indexURL: "https://cdn.jsdelivr.net/pyodide/v0.25.1/full/",
      });
      pyodideInstance.setStdout({
        batched: (text) => appendOutput(text),
      });
      pyodideInstance.setStderr({
        batched: (text) => {
          stderrBuffer += text;
          appendOutput(text, true);
        },
      });
    }
    const codeToRun = buildRunner(files[activeFile], stdinInput.value, useInputToggle.checked);
    await pyodideInstance.runPythonAsync(codeToRun);
    if (stderrBuffer) {
      const markers = parseTracebackMarkers(stderrBuffer, editor.getModel());
      setMarkers(markers);
    }
    status.textContent = "Done";
    statusRight.textContent = "Done";
  } catch (error) {
    appendOutput(error.message || String(error), true);
    const markers = parseTracebackMarkers(stderrBuffer || String(error), editor.getModel());
    setMarkers(markers);
    status.textContent = "Error";
    statusRight.textContent = "Error";
  } finally {
    runButton.disabled = false;
  }
};

const registerCompletion = () => {
  if (completionRegistered) {
    return;
  }
  completionRegistered = true;
  monaco.languages.registerCompletionItemProvider("python", {
    provideCompletionItems: () => ({
      suggestions: [
        {
          label: "fastio",
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: "import sys\n\ndata = sys.stdin.read().strip().split()\nif data:\n    it = iter(data)\n    n = int(next(it))\n    arr = [int(next(it)) for _ in range(n)]\n",
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          detail: "Fast input template",
        },
        {
          label: "readints",
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: "nums = list(map(int, sys.stdin.read().split()))\n",
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          detail: "Read all ints",
        },
        {
          label: "readline",
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: "sys.stdin.readline().strip()\n",
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          detail: "Read line",
        },
        {
          label: "recursionlimit",
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: "import sys\nsys.setrecursionlimit(10**7)\n",
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          detail: "Set recursion limit",
        },
      ],
    }),
  });
};

const initEditor = () => {
  const model = ensureModel(activeFile);
  editor = monaco.editor.create(editorContainer, {
    model,
    theme: theme === "light" ? "vs" : "vs-dark",
    automaticLayout: true,
    fontFamily: "Cascadia Mono, Fira Code, monospace",
    fontSize: 13,
    lineHeight: 22,
    minimap: { enabled: false },
    tabSize: 4,
    insertSpaces: true,
    scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
  });
  editor.onDidChangeCursorPosition(updateCursorInfo);
  editor.onDidChangeModel(() => {
    updateCursorInfo();
  });
  registerCompletion();
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, runCode);
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyN, () => {
    const name = window.prompt("New file name", "main.py");
    if (name !== null) {
      createFile(name, "");
    }
  });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyR, () => {
    const name = window.prompt("Rename file", activeFile);
    if (name !== null) {
      renameFile(activeFile, name);
    }
  });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyD, () => {
    deleteFile(activeFile);
  });
};

const initPyodide = () => {
  statusRight.textContent = "Loading";
  runButton.disabled = true;
  loadPyodide({
    indexURL: "https://cdn.jsdelivr.net/pyodide/v0.25.1/full/",
  })
    .then((instance) => {
      pyodideInstance = instance;
      pyodideInstance.setStdout({
        batched: (text) => appendOutput(text),
      });
      pyodideInstance.setStderr({
        batched: (text) => {
          stderrBuffer += text;
          appendOutput(text, true);
        },
      });
      status.textContent = "Interpreter ready";
      statusRight.textContent = "Ready";
      runButton.disabled = false;
    })
    .catch(() => {
      status.textContent = "Failed to load interpreter";
      statusRight.textContent = "Offline";
      runButton.disabled = false;
    });
};

const clampOutputWidth = (value) => {
  if (!workspace) {
    return value;
  }
  const rect = workspace.getBoundingClientRect();
  const minWidth = 260;
  const maxWidth = Math.max(minWidth, rect.width - 360);
  return Math.min(Math.max(value, minWidth), maxWidth);
};

const applyOutputWidth = (value) => {
  if (!workspace) {
    return;
  }
  const rect = workspace.getBoundingClientRect();
  if (!rect.width) {
    return;
  }
  const clamped = clampOutputWidth(value);
  workspace.style.setProperty("--output-width", `${clamped}px`);
  outputRatio = clamped / rect.width;
};

const applyOutputRatio = (ratio) => {
  if (!workspace || ratio == null) {
    return;
  }
  const rect = workspace.getBoundingClientRect();
  if (!rect.width) {
    return;
  }
  applyOutputWidth(rect.width * ratio);
};

const applyTheme = (nextTheme) => {
  theme = nextTheme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  if (themeToggle) {
    themeToggle.textContent = theme === "light" ? "Dark" : "Light";
  }
  if (editor) {
    monaco.editor.setTheme(theme === "light" ? "vs" : "vs-dark");
  }
  persistState();
};

const initSplitter = () => {
  if (!splitter || !workspace) {
    return;
  }
  let dragging = false;
  const onMove = (event) => {
    if (!dragging) {
      return;
    }
    const rect = workspace.getBoundingClientRect();
    const outputWidth = rect.right - event.clientX;
    applyOutputWidth(outputWidth);
  };
  const stop = () => {
    dragging = false;
    document.body.style.cursor = "";
    persistState();
  };
  splitter.addEventListener("mousedown", () => {
    dragging = true;
    document.body.style.cursor = "col-resize";
  });
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", stop);
};

runButton.addEventListener("click", runCode);
clearButton.addEventListener("click", clearOutput);
saveButton.addEventListener("click", saveFile);
if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    applyTheme(theme === "light" ? "dark" : "light");
  });
}
newFileButton.addEventListener("click", () => {
  const name = window.prompt("New file name", "main.py");
  if (name !== null) {
    createFile(name, "");
  }
});
renameFileButton.addEventListener("click", () => {
  const name = window.prompt("Rename file", activeFile);
  if (name !== null) {
    renameFile(activeFile, name);
  }
});
deleteFileButton.addEventListener("click", () => {
  deleteFile(activeFile);
});

fileList.addEventListener("dragover", (event) => event.preventDefault());
fileList.addEventListener("drop", (event) => {
  if (event.dataTransfer?.files?.length) {
    handleExternalDrop(event);
  } else {
    handleDropReorder(event);
  }
});

tabs.addEventListener("dragover", (event) => event.preventDefault());
tabs.addEventListener("drop", handleDropReorder);

editorContainer.addEventListener("dragover", (event) => event.preventDefault());
editorContainer.addEventListener("drop", handleExternalDrop);

stdinInput.value = savedState?.stdin ?? "";
useInputToggle.checked = savedState?.useInput ?? true;
stdinInput.addEventListener("input", persistState);
useInputToggle.addEventListener("change", persistState);

refreshFileList();
refreshTabs();
fileInfo.textContent = activeFile;
updateCursorInfo();
applyTheme(theme);

const legacyWidth = savedState?.outputWidth ? Number.parseFloat(savedState.outputWidth) : null;
if (legacyWidth) {
  applyOutputWidth(legacyWidth);
} else if (outputRatio != null) {
  applyOutputRatio(outputRatio);
} else {
  applyOutputWidth(360);
}

window.addEventListener("resize", () => {
  if (outputRatio != null) {
    applyOutputRatio(outputRatio);
  }
});

window.require.config({
  paths: {
    vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.47.0/min/vs",
    stackframe: "https://cdn.jsdelivr.net/npm/stackframe@1.3.4/dist/stackframe",
    "error-stack-parser": "https://cdn.jsdelivr.net/npm/error-stack-parser@2.1.4/dist/error-stack-parser",
  },
});
window.require(["vs/editor/editor.main"], () => {
  initEditor();
  initSplitter();
  applyTheme(theme);
});

initPyodide();

window.addEventListener("keydown", handleKeyPress, true);

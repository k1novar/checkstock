"use strict";
let state,
  formMode,
  pendingCommand,
  currentTab = "stock";
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const labels = {
  receipt: "Приход",
  reserve: "Резерв FBS",
  transfer: "Перемещение ФФ",
  fbo: "Отправка FBO",
  ship: "Отправка FBS",
  cancel: "Отмена резерва",
  receive: "Приёмка",
  deliver: "Доставка FBS",
  warehouse_create: "Создание ФФ",
  warehouse_rename: "Переименование ФФ",
  warehouse_archive: "Архивация ФФ",
};
const statuses = {
  reserved: "В резерве",
  transit: "В пути",
  received: "Принято",
  cancelled: "Отменено",
  delivered: "Доставлено",
};
const kindLabels = { fbs: "FBS", fbo: "FBO", transfer: "ФФ → ФФ" };
const product = (id) => state.products.find((p) => p.id === id);
const warehouse = (id) => state.warehouses.find((w) => w.id === id);
const sum = (fn) => state.products.reduce((n, p) => n + fn(p), 0);
const notice = (text, success = false) => {
  $("#notice").textContent = text;
  $("#notice").className = success ? "success" : "";
  $("#notice").hidden = false;
};
async function refresh() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok)
      throw Error(
        "Не удалось загрузить остатки. Проверьте подключение к серверу.",
      );
    state = await res.json();
    render();
    return true;
  } catch (e) {
    notice(e.message);
    return false;
  }
}
function render() {
  $("#product-count").textContent = state.products.length;
  const metrics = [
    [
      "Физически на ФФ",
      sum((p) => p.physical),
      "Включая резерв и архивные ФФ",
      "▦",
    ],
    ["Доступно", sum((p) => p.available), "На всех ФФ, включая архивные", "✓"],
    ["В резерве FBS", sum((p) => p.reserved), "Ожидает отправки клиенту", "◷"],
    [
      "Всего в пути",
      sum((p) => Object.values(p.transit).reduce((a, b) => a + b, 0)),
      "Между ФФ, на FBO и к клиентам",
      "↗",
    ],
  ];
  $("#metrics").innerHTML = metrics
    .map(
      ([title, value, note, icon]) =>
        `<div class="metric"><div class="metric-title">${title}<span class="metric-icon">${icon}</span></div><div class="metric-value">${value}<small>ед.</small></div><div class="metric-note">${note}</div></div>`,
    )
    .join("");
  renderStocks();
  $("#reconciliation").textContent = state.products.every((p) => p.balanced)
    ? "✓ Баланс сходится"
    : "⚠ Расхождение в учёте";
  $("#transit-cards").innerHTML = [
    [
      "transfer",
      "Между фулфилментами",
      "ФФ → ФФ",
      "Отправлено, ожидает приёмки на другом ФФ",
    ],
    [
      "fbo",
      "На маркетплейс",
      "FBO",
      "Покинуло ФФ, ещё не принято маркетплейсом",
    ],
    ["fbs", "К клиентам", "FBS", "Отправленные заказы, ожидают доставки"],
  ]
    .map(
      ([k, title, tag, desc]) =>
        `<div class="transit-card"><div class="transit-head"><h3>${title}</h3><span>${tag}</span></div><div class="transit-value">${sum((p) => p.transit[k])}<small>ед.</small></div><p>${desc}</p></div>`,
    )
    .join("");
  $("#final-stock").innerHTML = state.products
    .map(
      (p) =>
        `<span>${esc(p.sku)} · на маркетплейсе <b>${p.marketplace}</b> · доставлено <b>${p.delivered}</b></span>`,
    )
    .join("");
  $("#op-count").textContent = state.operations.filter((o) =>
    ["reserved", "transit"].includes(o.status),
  ).length;
  renderOperations();
  renderWarehouses();
  renderHistory();
}
function renderStocks() {
  const query = $("#search").value.toLocaleLowerCase("ru");
  const rows = state.products.filter((p) =>
    (p.sku + " " + p.name).toLocaleLowerCase("ru").includes(query),
  );
  $("#stock-table").innerHTML =
    `<thead><tr><th>ТОВАР / АРТИКУЛ</th>${state.warehouses.map((w) => `<th class="num">${esc(w.name.toUpperCase())}${w.archived ? " · АРХИВ" : ""}</th>`).join("")}<th class="num">НА ФФ</th><th class="num">В ПУТИ</th></tr></thead><tbody>${rows.map((p) => `<tr><td class="product"><b>${esc(p.name)}</b><small>${esc(p.sku)}</small></td>${p.stocks.map((c) => `<td class="num stock-cell"><strong>${c.available}</strong><span class="split">/</span><span class="res">${c.reserved}</span><small>физически ${c.physical}</small></td>`).join("")}<td class="num totals">${p.physical}</td><td class="num">${Object.values(p.transit).reduce((a, b) => a + b, 0)}</td></tr>`).join("") || `<tr><td colspan="${state.warehouses.length + 3}" class="empty">Товары не найдены</td></tr>`}</tbody>`;
}
function renderOperations() {
  const ops = state.operations.filter(
    (o) =>
      $("#operation-filter").value === "all" ||
      ["reserved", "transit"].includes(o.status),
  );
  $("#operation-table").innerHTML =
    `<thead><tr><th>№ / ТИП</th><th>ТОВАР</th><th>МАРШРУТ</th><th>КОЛ-ВО</th><th>СТАТУС</th><th>ДЕЙСТВИЕ</th></tr></thead><tbody>${
      ops
        .map((o) => {
          let actions = [];
          if (o.status === "reserved")
            actions = [
              ["ship", "Отправить"],
              ["cancel", "Отменить"],
            ];
          if (o.status === "transit")
            actions =
              o.kind === "fbs"
                ? [["deliver", "Доставлено"]]
                : [["receive", "Принять"]];
          return `<tr><td>#${o.id} · ${kindLabels[o.kind]}</td><td>${esc(product(o.product_id).sku)}<small>${esc(product(o.product_id).name)}</small></td><td>${esc(warehouse(o.source_id).name)}${warehouse(o.source_id).archived ? " (архив)" : ""}<small>→ ${esc(o.target_id ? warehouse(o.target_id).name : o.destination)}${o.target_id && warehouse(o.target_id).archived ? " (архив)" : ""}</small></td><td>${o.quantity}</td><td><span class="pill ${o.status}">${statuses[o.status]}</span></td><td><div class="row-actions">${actions.map(([a, t]) => `<button data-action="${a}" data-id="${o.id}">${t}</button>`).join("") || "—"}</div></td></tr>`;
        })
        .join("") ||
      '<tr><td colspan="6" class="empty">Здесь появятся резервы и отправки.<br>Создайте новую операцию.</td></tr>'
    }</tbody>`;
}
function renderWarehouses() {
  $("#warehouse-cards").innerHTML = state.warehouses
    .map((w) => {
      const cells = state.products.map((p) =>
        p.stocks.find((s) => s.warehouse_id === w.id),
      );
      return `<article class="warehouse-card ${w.archived ? "archived" : ""}"><span class="pill">${w.archived ? "Архив" : "Действующий ФФ"}</span><h3>${esc(w.name)}</h3><p>Физически: <b>${cells.reduce((n, c) => n + c.physical, 0)}</b> ед.<br>Резерв FBS: <b>${cells.reduce((n, c) => n + c.reserved, 0)}</b> ед.</p><div class="actions"><button data-warehouse="warehouse_rename" data-id="${w.id}">Переименовать</button>${w.archived ? "" : `<button class="danger" data-warehouse="warehouse_archive" data-id="${w.id}">Удалить в архив</button>`}</div></article>`;
    })
    .join("");
}
function renderHistory() {
  $("#history-table").innerHTML =
    `<thead><tr><th>ВРЕМЯ / №</th><th>ОПЕРАЦИЯ</th><th>АРТИКУЛ</th><th>КОЛ-ВО</th><th>ОТКУДА</th><th>КУДА</th></tr></thead><tbody>${state.events.map((e) => `<tr><td>${esc(new Date(e.at.endsWith("Z") || /[+-]\d\d:\d\d$/.test(e.at) ? e.at : e.at + "Z").toLocaleString("ru-RU"))}<small>#${e.id}${e.operation_id ? " · операция #" + e.operation_id : ""}</small></td><td>${labels[e.action]}</td><td>${e.product_id ? esc(product(e.product_id).sku) : "—"}</td><td>${e.quantity || "—"}</td><td>${esc(e.source) || "—"}</td><td>${esc(e.target) || "—"}</td></tr>`).join("")}</tbody>`;
}
function tab(name) {
  currentTab = name;
  document
    .querySelectorAll('section[id^="tab-"]')
    .forEach((s) => (s.hidden = s.id !== "tab-" + name));
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  const names = {
    stock: [
      "Где сейчас товар?",
      "Остатки",
      "Остатки на фулфилментах, резервы и товары в пути — в одном месте.",
    ],
    operations: [
      "Отправки и резервы",
      "Отправки и резервы",
      "Завершайте операции и отслеживайте каждую отправку.",
    ],
    warehouses: [
      "Склады",
      "Склады",
      "Действующие и архивные фулфилменты. Остатки всегда на виду.",
    ],
    history: [
      "История операций",
      "История операций",
      "Полный журнал движения товаров и изменений складов.",
    ],
  };
  $("#page-title").textContent = names[name][0];
  $("#breadcrumb").textContent = names[name][1];
  $("#page-description").textContent = names[name][2];
}
const selectField = (name, label, options) =>
  `<label class="field">${label}<select name="${name}" required>${options}</select></label>`;
const option = (v, t) => `<option value="${esc(v)}">${esc(t)}</option>`;
function movementFields(action = "receipt") {
  const all = state.warehouses.filter(
    (w) => action === "transfer" || !w.archived,
  );
  $("#form-fields").innerHTML =
    selectField(
      "action",
      "Тип операции",
      ["receipt", "reserve", "transfer", "fbo"]
        .map((a) => option(a, labels[a]))
        .join(""),
    ) +
    selectField(
      "product_id",
      "Товар",
      state.products.map((p) => option(p.id, p.sku + " · " + p.name)).join(""),
    ) +
    selectField(
      "warehouse_id",
      action === "receipt" ? "Склад приёмки" : "Склад отправки",
      all
        .map((w) => option(w.id, w.name + (w.archived ? " (архив)" : "")))
        .join(""),
    ) +
    (action === "transfer"
      ? selectField(
          "target_id",
          "Куда переместить",
          state.warehouses
            .filter((w) => !w.archived)
            .map((w) => option(w.id, w.name))
            .join(""),
        )
      : "") +
    `<label class="field">Количество, ед.<input name="quantity" type="number" min="1" max="1000000" step="1" required value="1"></label>` +
    (["reserve", "fbo"].includes(action)
      ? `<label class="field">${action === "reserve" ? "Номер заказа FBS" : "Склад маркетплейса"}<input name="destination" maxlength="120" required placeholder="${action === "reserve" ? "Заказ № 1001" : "WB · Коледино"}"></label>`
      : "") +
    '<div class="form-hint" id="availability"></div>';
  $('#form-fields [name="action"]').value = action;
  if (action === "transfer") {
    const from = Number($('#form-fields [name="warehouse_id"]').value);
    const target = $('#form-fields [name="target_id"]');
    const other = [...target.options].find((o) => Number(o.value) !== from);
    if (other) target.value = other.value;
  }
  updateAvailability();
}
function updateAvailability() {
  const root = $("#form-fields");
  if (!root.querySelector('[name="product_id"]')) return;
  const pid = Number(root.querySelector('[name="product_id"]').value),
    wid = Number(root.querySelector('[name="warehouse_id"]').value);
  const cell = product(pid)?.stocks.find((c) => c.warehouse_id === wid);
  $("#availability").textContent = cell
    ? `На выбранном ФФ: ${cell.physical} физически · ${cell.reserved} в резерве · ${cell.available} доступно.`
    : "Нет подходящих складов. Сначала добавьте ФФ.";
}
function openDialog(mode, id) {
  if (!state) return;
  if (localStorage.getItem("checkstock-pending")) {
    recoverPending().then(refresh);
    notice(
      "Проверяем предыдущий запрос. После проверки снова выберите действие.",
    );
    return;
  }
  formMode = { mode, id };
  pendingCommand = null;
  $("#form-error").hidden = true;
  $("#submit-command").disabled = false;
  $("#submit-command").textContent = "Подтвердить";
  $("#dialog-title").textContent =
    mode === "movement" ? "Новая операция" : labels[mode];
  if (mode === "movement") movementFields();
  else if (mode === "warehouse_create" || mode === "warehouse_rename")
    $("#form-fields").innerHTML =
      `<label class="field">Название ФФ<input name="name" required maxlength="120" value="${mode === "warehouse_rename" ? esc(warehouse(id).name) : ""}" placeholder="Например, Восток"></label>`;
  else if (mode === "warehouse_archive")
    $("#form-fields").innerHTML =
      `<div class="danger-note">Склад «${esc(warehouse(id).name)}» будет перемещён в архив. Остатки и история сохранятся. Новые приходы и резервы будут недоступны; вывоз остатка и завершение начатых операций останутся доступны.</div>`;
  else {
    const op = state.operations.find((o) => o.id === id);
    $("#form-fields").innerHTML =
      `<div class="form-hint">Операция #${op.id} · ${kindLabels[op.kind]}<br>${esc(product(op.product_id).name)} · <b>${op.quantity} ед.</b><br>${esc(warehouse(op.source_id).name)} → ${esc(op.target_id ? warehouse(op.target_id).name : op.destination)}</div>`;
  }
  $("#dialog").showModal();
}
$("#command-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (pendingCommand && localStorage.getItem("checkstock-pending")) {
    const saved = JSON.parse(localStorage.getItem("checkstock-pending"));
    if (JSON.stringify(saved) !== JSON.stringify(pendingCommand.payload))
      return;
  }
  const button = $("#submit-command");
  button.disabled = true;
  $("#form-error").hidden = true;
  const data = Object.fromEntries(new FormData(e.target));
  for (const k of ["quantity", "product_id", "warehouse_id", "target_id"])
    if (k in data) data[k] = Number(data[k]);
  if (formMode.mode !== "movement") {
    data.action = formMode.mode;
    if (formMode.id)
      data[
        formMode.mode.startsWith("warehouse_") ? "warehouse_id" : "operation_id"
      ] = formMode.id;
  }
  const signature = JSON.stringify(data);
  if (
    !localStorage.getItem("checkstock-pending") &&
    (!pendingCommand || pendingCommand.signature !== signature)
  )
    pendingCommand = {
      signature,
      payload: { ...data, request_id: crypto.randomUUID() },
    };
  // Save before network I/O. A reload after a lost response must not create another movement.
  localStorage.setItem(
    "checkstock-pending",
    JSON.stringify(pendingCommand.payload),
  );
  try {
    const res = await fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pendingCommand.payload),
    });
    const result = await res.json();
    if (!res.ok) {
      if (res.status < 500) localStorage.removeItem("checkstock-pending");
      let message =
        typeof result.detail === "string"
          ? result.detail
          : "Проверьте поля формы: нужны положительные целые количества и заполненные значения.";
      if (result.warehouses)
        message +=
          "\n" +
          result.warehouses
            .map(
              (w) =>
                `${w.warehouse}${w.archived ? " (архив)" : ""}: ${w.available} доступно`,
            )
            .join(" · ");
      throw Error(message);
    }
    localStorage.removeItem("checkstock-pending");
    pendingCommand = null;
    $("#dialog").close();
    notice(result.message, true);
    await refresh();
  } catch (error) {
    if (localStorage.getItem("checkstock-pending")) {
      document
        .querySelectorAll("#form-fields input,#form-fields select")
        .forEach((el) => (el.readOnly = true));
      $("#form-error").textContent =
        "Ответ не подтверждён. Закройте окно и снова нажмите «Новая операция», чтобы проверить прежний запрос. " +
        error.message;
      button.textContent = "Проверить запрос";
    } else $("#form-error").textContent = error.message;
    $("#form-error").hidden = false;
  } finally {
    button.disabled = false;
  }
});
async function recoverPending() {
  const saved = localStorage.getItem("checkstock-pending");
  if (!saved) return;
  try {
    const payload = JSON.parse(saved);
    const res = await fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await res.json();
    if (res.ok || res.status < 500)
      localStorage.removeItem("checkstock-pending");
    notice(
      res.ok
        ? "Предыдущая операция подтверждена. Повторного списания нет."
        : typeof result.detail === "string"
          ? result.detail
          : "Предыдущая операция не проведена.",
      res.ok,
    );
  } catch {
    notice(
      "Не удалось проверить предыдущую операцию. Обновите страницу до создания новых движений.",
    );
  }
}
document.addEventListener("click", (e) => {
  const tabButton = e.target.closest("[data-tab]");
  if (tabButton) tab(tabButton.dataset.tab);
  const action = e.target.closest("[data-action]");
  if (action) openDialog(action.dataset.action, Number(action.dataset.id));
  const wh = e.target.closest("[data-warehouse]");
  if (wh) openDialog(wh.dataset.warehouse, Number(wh.dataset.id));
});
$("#form-fields").addEventListener("change", (e) => {
  if (e.target.name === "action") movementFields(e.target.value);
  else updateAvailability();
});
$("#search").addEventListener("input", () => state && renderStocks());
$("#operation-filter").addEventListener(
  "change",
  () => state && renderOperations(),
);
$("#refresh").onclick = refresh;
$("#new-operation").onclick = () => openDialog("movement");
$("#add-warehouse").onclick = () => openDialog("warehouse_create");
$("#close-dialog").onclick = $("#cancel-dialog").onclick = () =>
  $("#dialog").close();
(async () => {
  await recoverPending();
  await refresh();
})();

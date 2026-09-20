/* Progressive enhancement only. Trading and configuration forms stay server-owned. */
(() => {
  'use strict';
  document.documentElement.classList.add('enhanced');
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const preferences = {
    get(key, fallback) { try { return localStorage.getItem(`prism.ui.${key}`) || fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(`prism.ui.${key}`, value); } catch { /* Optional preferences. */ } }
  };
  let toastTimer;
  function notify(message) {
    const toast = $('.toast');
    toast.textContent = message; toast.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); notify('Copied to clipboard.'); }
    catch { notify('Clipboard unavailable. Select the text and copy manually.'); }
  }
  const theme = $('#console-theme');
  const media = matchMedia('(prefers-color-scheme: light)');
  function applyTheme() {
    document.documentElement.dataset.theme = theme.value === 'system' ? (media.matches ? 'light' : 'dark') : theme.value;
  }
  const savedTheme = preferences.get('theme', 'system');
  theme.value = ['system', 'dark', 'light'].includes(savedTheme) ? savedTheme : 'system';
  applyTheme(); media.addEventListener('change', applyTheme);
  theme.addEventListener('change', () => { preferences.set('theme', theme.value); applyTheme(); });
  const density = $('[data-density]');
  function applyDensity(compact) {
    document.documentElement.classList.toggle('compact', compact);
    density.setAttribute('aria-pressed', String(compact));
  }
  applyDensity(preferences.get('compact', 'false') === 'true');
  density.addEventListener('click', () => {
    const compact = density.getAttribute('aria-pressed') !== 'true';
    applyDensity(compact); preferences.set('compact', String(compact));
  });

  const dialog = $('#command-dialog');
  const query = $('#command-query');
  function filterCommands() {
    const links = $$('.command-results a');
    links.forEach(link => { link.hidden = !link.textContent.toLowerCase().includes(query.value.toLowerCase().trim()); });
    $('[data-command-empty]').hidden = links.some(link => !link.hidden);
  }
  function openCommands() { if (!dialog.open) { query.value = ''; filterCommands(); dialog.showModal(); query.focus(); } }
  $('[data-command-open]').addEventListener('click', openCommands);
  $('[data-command-close]').addEventListener('click', () => dialog.close());
  query.addEventListener('input', filterCommands);
  query.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { event.preventDefault(); $('.command-results a:not([hidden])')?.focus(); }
    if (event.key === 'Enter') { event.preventDefault(); $('.command-results a:not([hidden])')?.click(); }
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openCommands(); }
  });

  function updateTimes() {
    const now = new Date();
    $$('[data-clock]').forEach(clock => {
      clock.textContent = new Intl.DateTimeFormat('en-GB', { timeZone: clock.dataset.clock, hour: '2-digit', minute: '2-digit' }).format(now);
      clock.dateTime = now.toISOString();
    });
    $$('[data-due]').forEach(label => {
      const due = Date.parse(label.dataset.due);
      if (!Number.isFinite(due)) { label.textContent = 'Timing unavailable'; return; }
      const minutes = Math.ceil((due - now.getTime()) / 60000);
      label.textContent = minutes <= 0 ? 'Scheduled time reached' : minutes < 60 ? `In ${minutes} min` : minutes < 1440 ? `In ${Math.ceil(minutes / 60)} hr` : `In ${Math.ceil(minutes / 1440)} days`;
      label.classList.toggle('due-now', minutes <= 0);
    });
  }
  updateTimes(); setInterval(updateTimes, 30000);

  // Only read-only GET pages expose refresh; never replay a POST or interrupt edits.
  const refresh = $('#refresh-interval');
  if (refresh) {
    let timer; let dirty = false;
    document.addEventListener('input', event => { if (event.target.closest('form, [data-table-panel]')) dirty = true; });
    document.addEventListener('change', event => { if (event.target.closest('form, [data-table-panel]')) dirty = true; });
    const status = $('[data-refresh-status]');
    $('[data-refresh]').addEventListener('click', () => { location.assign(location.pathname + location.search); });
    refresh.addEventListener('change', () => {
      clearInterval(timer);
      const seconds = Number(refresh.value);
      status.textContent = seconds ? `Auto refresh every ${seconds}s` : 'Auto refresh off';
      if (seconds) timer = setInterval(() => {
        if (document.hidden || dialog.open || dirty || document.activeElement?.matches('input, textarea, select')) {
          status.textContent = 'Refresh paused while hidden or editing'; return;
        }
        // A safe GET snapshot. Opt-in preference survives this refresh only via sessionStorage.
        try { sessionStorage.setItem('prism.refresh', JSON.stringify({ path: location.pathname, seconds })); } catch { /* optional */ }
        location.assign(location.pathname + location.search);
      }, seconds * 1000);
    });
    try {
      const saved = JSON.parse(sessionStorage.getItem('prism.refresh') || 'null');
      sessionStorage.removeItem('prism.refresh');
      if (saved?.path === location.pathname && [30, 60].includes(saved.seconds)) {
        refresh.value = String(saved.seconds); refresh.dispatchEvent(new Event('change'));
      }
    } catch { /* optional preferences */ }
  }

  function csvCell(value) {
    // Spreadsheet formula injection protection, including whitespace prefixes.
    const safe = /^[\s]*[=+@-]/.test(value) || /^[\t\r\n]/.test(value) ? `'${value}` : value;
    return `"${safe.replaceAll('"', '""')}"`;
  }
  $$('[data-table-panel]').forEach(panel => {
    const table = $('[data-interactive-table]', panel);
    if (!table) return;
    const body = table.tBodies[0]; const rows = Array.from(body.rows);
    const search = $('[data-table-search]', panel); const filters = $$('[data-table-filter]', panel);
    let filtered = rows.slice(); let page = 0; let sortColumn = -1; let direction = 1;
    // Capture immutable display values before adding controls/countdown labels.
    const values = new Map(rows.map(row => [row, Array.from(row.cells, cell => {
      const clone = cell.cloneNode(true); $$('[data-due]', clone).forEach(el => el.remove());
      return clone.textContent.trim();
    })]));
    const footer = document.createElement('div'); footer.className = 'table-footer';
    const count = document.createElement('span'); count.setAttribute('role', 'status');
    const sizeLabel = document.createElement('label'); sizeLabel.textContent = 'Rows per page ';
    const size = document.createElement('select');
    [25, 50, 100].forEach(value => size.add(new Option(String(value), String(value)))); sizeLabel.append(size);
    const previous = document.createElement('button'); previous.type = 'button'; previous.textContent = 'Previous'; previous.className = 'btn ghost';
    const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Next'; next.className = 'btn ghost';
    footer.append(count, sizeLabel, previous, next); panel.append(footer);
    const empty = document.createElement('p'); empty.className = 'empty-state'; empty.textContent = 'No records match these filters.'; empty.hidden = true; panel.insertBefore(empty, footer);
    function render() {
      const term = search.value.toLocaleLowerCase().trim();
      filtered = rows.filter(row => {
        const cells = values.get(row);
        return cells.join(' ').toLocaleLowerCase().includes(term) && filters.every(filter => !filter.value || cells[Number(filter.dataset.tableFilter)] === filter.value);
      });
      if (sortColumn >= 0) filtered.sort((a, b) => {
        const left = values.get(a)[sortColumn]; const right = values.get(b)[sortColumn];
        if (table.tHead.rows[0].cells[sortColumn].dataset.sortType === 'date') {
          const x = Date.parse(left); const y = Date.parse(right);
          if (!Number.isFinite(x)) return Number.isFinite(y) ? 1 : 0;
          if (!Number.isFinite(y)) return -1;
          return direction * (x - y);
        }
        return direction * left.localeCompare(right, undefined, { numeric: true });
      });
      const limit = Number(size.value); const pages = Math.max(1, Math.ceil(filtered.length / limit)); page = Math.min(page, pages - 1);
      rows.forEach(row => { row.hidden = true; });
      filtered.forEach((row, index) => { body.append(row); row.hidden = index < page * limit || index >= (page + 1) * limit; });
      count.textContent = `${filtered.length} matching / ${rows.length} loaded · Page ${page + 1} of ${pages}`;
      previous.disabled = page === 0; next.disabled = page >= pages - 1; empty.hidden = filtered.length !== 0;
    }
    search.addEventListener('input', () => { page = 0; render(); });
    filters.forEach(filter => filter.addEventListener('change', () => { page = 0; render(); }));
    size.addEventListener('change', () => { page = 0; render(); });
    previous.addEventListener('click', () => { page--; render(); }); next.addEventListener('click', () => { page++; render(); });
    Array.from(table.tHead.rows[0].cells).forEach((header, index) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'sort-button';
      button.textContent = header.textContent; button.title = `Sort by ${header.textContent}`; header.textContent = ''; header.append(button);
      button.addEventListener('click', () => {
        direction = sortColumn === index ? -direction : 1; sortColumn = index; page = 0;
        Array.from(table.tHead.rows[0].cells).forEach(th => th.removeAttribute('aria-sort'));
        header.setAttribute('aria-sort', direction === 1 ? 'ascending' : 'descending'); render();
      });
    });
    $('[data-export-csv]', panel).addEventListener('click', event => {
      const headers = Array.from(table.tHead.rows[0].cells, cell => cell.textContent.trim());
      const csv = [headers, ...filtered.map(row => values.get(row))].map(cells => cells.map(csvCell).join(',')).join('\r\n');
      const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `prism-${event.currentTarget.dataset.exportCsv}.csv`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify(`Exported ${filtered.length} filtered records.`);
    });
    render();
  });

  const examples = {
    'kr-buy': { type: 'BUY', market: 'KR', ticker: '005930', company_name: 'Samsung Electronics', price: 70000 },
    'us-buy': { type: 'BUY', market: 'US', ticker: 'AAPL', company_name: 'Apple', price: 190.5 },
    'us-sell': { type: 'SELL', market: 'US', ticker: 'AAPL', company_name: 'Apple', price: 195 },
    event: { type: 'EVENT', market: 'US', ticker: 'AAPL', company_name: 'Apple', event_type: 'NEWS', event_description: 'Example event for validation only' }
  };
  $$('[data-editor-tools]').forEach(toolbar => {
    const editor = document.getElementById(toolbar.dataset.editorTools);
    $('[data-example]', toolbar).addEventListener('change', event => {
      const example = examples[event.target.value];
      if (example && (!editor.value.trim() || confirm('Replace the current input with this example?'))) {
        editor.value = JSON.stringify(example, null, 2); editor.focus(); notify('Example loaded. Prices are illustrative. Review before validating.');
      }
      event.target.value = '';
    });
    $('[data-format-json]', toolbar).addEventListener('click', () => {
      try { editor.value = JSON.stringify(JSON.parse(editor.value), null, 2); notify('JSON formatted.'); }
      catch { notify('Input is not valid JSON. Telegram text can still be validated in Signal Studio.'); }
    });
    $('[data-copy-editor]', toolbar).addEventListener('click', () => copy(editor.value));
    $('[data-clear-editor]', toolbar).addEventListener('click', () => { if (!editor.value || confirm('Clear the current input?')) { editor.value = ''; editor.focus(); } });
  });
  $$('[data-copy-target]').forEach(button => button.addEventListener('click', () => copy(document.getElementById(button.dataset.copyTarget).textContent)));
  $('[data-log-wrap]')?.addEventListener('click', event => {
    const wrap = event.currentTarget.getAttribute('aria-pressed') !== 'true';
    event.currentTarget.setAttribute('aria-pressed', String(wrap)); $('#log-output').classList.toggle('no-wrap', !wrap);
  });
  // Only a presentation filter; account selection and execution stay unchanged.
  const accounts = $('.panel--routes .account-list');
  if (accounts && $('.account-card', accounts)) {
    const label = document.createElement('label'); label.className = 'account-search'; label.textContent = 'Find an account route';
    const input = document.createElement('input'); input.type = 'search'; input.placeholder = 'Name, market or mode'; label.append(input); accounts.before(label);
    const empty = document.createElement('p'); empty.textContent = 'No matching account routes.'; empty.hidden = true; accounts.after(empty);
    input.addEventListener('input', () => {
      const cards = $$('.account-card', accounts); cards.forEach(card => { card.hidden = !card.textContent.toLowerCase().includes(input.value.toLowerCase().trim()); }); empty.hidden = cards.some(card => !card.hidden);
    });
  }
})();

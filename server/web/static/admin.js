(() => {
   document.querySelectorAll('[data-open-modal]').forEach(button => button.addEventListener('click', () => {
      document.getElementById(button.dataset.openModal)?.showModal();
   }));
   document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
      if (event.target === dialog) dialog.close();
   }));
   const taskModal = document.querySelector('#task-modal');
   if (taskModal?.querySelector('[name="id"]').value) taskModal.showModal();

   const capability = document.querySelector('#action-capability');
   const parameters = document.querySelector('#action-parameters');
   capability?.addEventListener('change', () => {
      const example = capability.selectedOptions[0].dataset.parameters;
      if (example) parameters.value = JSON.stringify(JSON.parse(example), null, 2);
   });
   document.querySelectorAll('.copy-button').forEach(button => button.addEventListener('click', async () => {
      await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).textContent.trim());
      button.textContent = 'Kopiert';
   }));
   document.querySelectorAll('.stored-token-copy').forEach(button => button.addEventListener('click', async () => {
      const body = new FormData();
      body.append('csrf', button.dataset.csrf);
      const response = await fetch(button.dataset.url, {method: 'POST', body});
      if (!response.ok) return alert(await response.text() || 'Token konnte nicht kopiert werden.');
      await navigator.clipboard.writeText((await response.json()).token);
      button.textContent = 'Kopiert';
   }));

   const table = document.querySelector('#client-table');
   const body = table?.tBodies[0];
   const search = document.querySelector('#client-search');
   const filters = {entryType: '', platform: ''};
   const count = document.querySelector('#client-result-count');
   let sortKey = 'hostname';
   let sortAscending = true;
   const rows = () => body ? [...body.querySelectorAll('tr[data-entry]')] : [];
   const clientRows = () => rows().filter(row => row.dataset.clientId);

   const applyView = () => {
      if (!table) return;
      const terms = search.value.trim().toLocaleLowerCase('de-DE').split(/\s+/).filter(Boolean);
      let visible = 0;
      rows().forEach(row => {
         const show = terms.every(term => row.dataset.search.includes(term)) &&
            (!filters.platform || row.dataset.platform.toLocaleLowerCase('de-DE') === filters.platform.toLocaleLowerCase('de-DE')) &&
            (!filters.entryType || row.dataset.entryType === filters.entryType);
         row.hidden = !show;
         if (show) visible += 1;
      });
      count.textContent = `${visible} von ${rows().length} Einträgen`;
   };

   const sortRows = key => {
      sortAscending = key === sortKey ? !sortAscending : true;
      sortKey = key;
      rows().sort((left, right) => {
         const a = left.dataset[key] || '';
         const b = right.dataset[key] || '';
         const result = ['last_seen', 'status'].includes(key) ? Number(a) - Number(b) : a.localeCompare(b, 'de');
         return sortAscending ? result : -result;
      }).forEach(row => body.appendChild(row));
   };

   search?.addEventListener('input', applyView);
   document.querySelectorAll('.filter-buttons[data-filter]').forEach(group => group.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button) return;
      filters[group.dataset.filter] = button.dataset.value;
      group.querySelectorAll('button').forEach(item => {
         item.classList.toggle('active', item === button);
         item.setAttribute('aria-pressed', String(item === button));
      });
      applyView();
   }));
   document.querySelectorAll('.sort-button').forEach(button => button.addEventListener('click', () => {
      sortRows(button.dataset.sort);
      document.querySelectorAll('.sort-button').forEach(item => {
         item.classList.toggle('active', item === button);
         item.setAttribute('aria-sort', item === button ? (sortAscending ? 'ascending' : 'descending') : 'none');
      });
   }));

   const executeModal = document.querySelector('#execute-modal');
   const executionTime = document.querySelector('#execution-time');
   const scheduledAt = document.querySelector('#scheduled-at');
   const executeCapability = document.querySelector('#execute-capability');
   document.querySelectorAll('.execute-client').forEach(button => button.addEventListener('click', () => {
      document.querySelector('#execute-target').value = button.dataset.deviceId;
      document.querySelector('#execute-client-title').textContent = `Code auf ${button.dataset.deviceName} ausführen`;
      const capabilities = JSON.parse(button.dataset.capabilities);
      executeCapability.replaceChildren(...capabilities.map(capability => {
         const option = document.createElement('option');
         option.value = capability.id;
         option.textContent = capability.title;
         option.dataset.parameters = JSON.stringify(capability.parameters);
         return option;
      }));
      document.querySelector('#execute-empty').hidden = capabilities.length > 0;
      document.querySelector('#execute-submit').disabled = capabilities.length === 0;
      executeCapability.dispatchEvent(new Event('change'));
      executeModal.showModal();
   }));
   executionTime?.addEventListener('change', () => {
      document.querySelector('#scheduled-field').hidden = executionTime.value !== 'scheduled';
      scheduledAt.required = executionTime.value === 'scheduled';
   });
   executeModal?.querySelector('form.modal-form').addEventListener('submit', () => {
      document.querySelector('#execute-run-at').value = executionTime.value === 'scheduled'
         ? String(Math.floor(new Date(scheduledAt.value).getTime() / 1000)) : '';
   });
   executeCapability?.addEventListener('change', () => {
      const example = executeCapability.selectedOptions[0]?.dataset.parameters;
      document.querySelector('#execute-parameters').value = example ? JSON.stringify(JSON.parse(example), null, 2) : '{}';
   });

   const formatTime = timestamp => timestamp
      ? new Date(timestamp * 1000).toLocaleString('de-DE', {dateStyle: 'short', timeStyle: 'medium'})
      : '–';

   const setActionResult = (cell, result) => {
      cell.replaceChildren();
      if (result === null) {
         const pending = document.createElement('span');
         pending.className = 'muted';
         pending.textContent = 'Ausstehend';
         cell.appendChild(pending);
         return;
      }
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      const output = document.createElement('pre');
      summary.textContent = 'Anzeigen';
      output.textContent = JSON.stringify(result, null, 2);
      details.append(summary, output);
      cell.appendChild(details);
   };

   const updateActions = actions => {
      const actionBody = document.querySelector('#action-table tbody');
      if (!actionBody) return;
      const knownRows = new Map([...actionBody.querySelectorAll('[data-action-id]')]
         .map(row => [Number(row.dataset.actionId), row]));
      actionBody.querySelector('.action-empty')?.remove();
      actions.slice().reverse().forEach(action => {
         let row = knownRows.get(action.id);
         if (!row) {
            row = document.createElement('tr');
            row.dataset.actionId = action.id;
            for (let index = 0; index < 7; index += 1) row.appendChild(document.createElement('td'));
            actionBody.prepend(row);
         }
         const actionState = JSON.stringify(action);
         if (row.dataset.actionState === actionState) return;
         row.dataset.actionState = actionState;
         const cells = row.cells;
         cells[0].textContent = `#${action.id}`;
         cells[1].textContent = action.hostname;
         cells[2].textContent = action.capability_id;
         cells[3].replaceChildren();
         const status = document.createElement('span');
         status.className = `pill ${action.status === 'done' ? 'good' : action.status === 'failed' ? 'bad' : ''}`.trim();
         status.textContent = action.status;
         cells[3].appendChild(status);
         cells[4].textContent = formatTime(action.run_at);
         cells[5].textContent = formatTime(action.finished_at);
         setActionResult(cells[6], action.result);
         row.title = action.execution_device_id
            ? `Übertragen/ausgeführt über ${action.execution_platform || 'unbekanntes Betriebssystem'} (Token ${action.execution_device_id})`
            : 'Noch an keinen Token übertragen';
      });
      knownRows.forEach((row, id) => {
         if (!actions.some(action => action.id === id)) row.remove();
      });
   };

   const updateStatus = async () => {
      const indicators = [document.querySelector('#client-refresh-state'), document.querySelector('#action-refresh-state')].filter(Boolean);
      try {
         const response = await fetch('/admin/client-status', {headers: {'Accept': 'application/json'}});
         if (!response.ok) throw new Error(`HTTP ${response.status}`);
         const data = await response.json();
         if (table) {
            const currentIds = clientRows().map(row => row.dataset.clientId).sort().join(',');
            const newIds = data.devices.map(device => device.id).sort().join(',');
            if (currentIds !== newIds || data.devices.some(device => {
               const row = body.querySelector(`[data-client-id="${CSS.escape(device.id)}"]`);
               return row.dataset.entryType !== (device.is_image_source ? 'template' : 'client');
            })) return location.reload();
            data.devices.forEach(device => {
               const row = body.querySelector(`[data-client-id="${CSS.escape(device.id)}"]`);
               const status = row.querySelector('[data-field="status"]');
               status.querySelector('.dot').classList.toggle('online', device.online);
               status.querySelector('span:last-child').textContent = device.online ? 'online' : 'offline';
               for (const [field, value] of Object.entries({hostname: device.hostname, platform: device.platform, agent: device.agent_version, last_seen: device.last_seen_text})) {
                  row.querySelector(`[data-field="${field}"]`).textContent = value || '–';
               }
               Object.assign(row.dataset, {status: device.online ? '1' : '0', hostname: device.hostname.toLowerCase(), platform: device.platform,
                  agent: device.agent_version.toLowerCase(), last_seen: String(device.last_seen), search: JSON.stringify(device).toLowerCase()});
               document.querySelectorAll(`[data-status-device-id="${CSS.escape(device.id)}"]`).forEach(dot => dot.classList.toggle('online', device.online));
               document.querySelectorAll(`[data-last-seen-device-id="${CSS.escape(device.id)}"]`).forEach(element => { element.textContent = device.last_seen_text; });
            });
            applyView();
         }
         updateActions(data.actions);
         indicators.forEach(indicator => { indicator.textContent = `Zuletzt aktualisiert: ${new Date().toLocaleTimeString('de-DE')}`; });
      } catch (error) {
         indicators.forEach(indicator => { indicator.textContent = 'Statusaktualisierung vorübergehend nicht verfügbar'; });
      }
   };

   const logTable = document.querySelector('#log-table');
   const logBody = logTable?.tBodies[0];
   const logRows = () => logBody ? [...logBody.querySelectorAll('tr[data-log]')] : [];
   const selectedAspects = new Set(['registration', 'token', 'action']);
   const applyLogView = () => {
      if (!logTable) return;
      const terms = document.querySelector('#log-search').value.trim().toLocaleLowerCase('de-DE').split(/\s+/).filter(Boolean);
      const client = document.querySelector('#log-client').value;
      const action = document.querySelector('#log-action').value;
      const periodDays = Number(document.querySelector('#log-period').value);
      const cutoff = periodDays ? Date.now() / 1000 - periodDays * 86400 : 0;
      let visible = 0;
      logRows().forEach(row => {
         const show = selectedAspects.has(row.dataset.aspect) && (!client || row.dataset.client === client) &&
            (!action || row.dataset.action === action) && Number(row.dataset.timestamp) >= cutoff &&
            terms.every(term => row.dataset.search.includes(term));
         row.hidden = !show;
         if (show) visible += 1;
      });
      document.querySelector('#log-result-count').textContent = `${visible} von ${logRows().length} Einträgen`;
   };
   document.querySelector('#log-search')?.addEventListener('input', applyLogView);
   document.querySelector('#log-client')?.addEventListener('change', applyLogView);
   document.querySelector('#log-action')?.addEventListener('change', applyLogView);
   document.querySelector('#log-period')?.addEventListener('change', applyLogView);
   document.querySelector('#log-aspects')?.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button) return;
      button.classList.toggle('active');
      button.setAttribute('aria-pressed', String(button.classList.contains('active')));
      if (button.classList.contains('active')) selectedAspects.add(button.dataset.value);
      else selectedAspects.delete(button.dataset.value);
      applyLogView();
   });
   document.querySelectorAll('.log-sort').forEach(button => button.addEventListener('click', () => {
      const wasAscending = button.getAttribute('aria-sort') === 'ascending';
      const ascending = button.classList.contains('active') ? !wasAscending : button.dataset.sort !== 'timestamp';
      logRows().sort((left, right) => {
         const a = left.dataset[button.dataset.sort];
         const b = right.dataset[button.dataset.sort];
         const result = button.dataset.sort === 'timestamp' ? Number(a) - Number(b) : a.localeCompare(b, 'de');
         return ascending ? result : -result;
      }).forEach(row => logBody.appendChild(row));
      document.querySelectorAll('.log-sort').forEach(item => {
         item.classList.toggle('active', item === button);
         item.setAttribute('aria-sort', item === button ? (ascending ? 'ascending' : 'descending') : 'none');
      });
   }));

   applyView();
   applyLogView();
   if (table || document.querySelector('#action-table')) {
      updateStatus();
      setInterval(updateStatus, 5000);
   }
})();

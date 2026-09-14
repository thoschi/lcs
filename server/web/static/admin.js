(() => {
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
   const platform = document.querySelector('#platform-filter');
   const deviceType = document.querySelector('#client-type-filter');
   const group = document.querySelector('#group-filter');
   const count = document.querySelector('#client-result-count');
   let sortKey = 'hostname';
   let sortAscending = true;
   const rows = () => body ? [...body.querySelectorAll('tr[data-client-id]')] : [];

   const applyView = () => {
      if (!table) return;
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      rows().forEach(row => {
         const show = (!query || row.dataset.search.includes(query)) &&
            (!platform.value || row.dataset.platform === platform.value) &&
            (!deviceType.value || row.dataset.clientType === deviceType.value) &&
            (!group.value || row.dataset.groups.split(', ').includes(group.value));
         row.hidden = !show;
         if (show) visible += 1;
      });
      count.textContent = `${visible} von ${rows().length} Clients`;
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
   platform?.addEventListener('change', applyView);
   deviceType?.addEventListener('change', applyView);
   group?.addEventListener('change', applyView);
   document.querySelectorAll('.sort-button').forEach(button => button.addEventListener('click', () => sortRows(button.dataset.sort)));

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
            const currentIds = rows().map(row => row.dataset.clientId).sort().join(',');
            const newIds = data.devices.map(device => device.id).sort().join(',');
            if (currentIds !== newIds || data.devices.some(device => {
               const row = body.querySelector(`[data-client-id="${CSS.escape(device.id)}"]`);
               return row.dataset.clientType !== (device.is_image_source ? 'template' : 'client');
            })) return location.reload();
            data.devices.forEach(device => {
               const row = body.querySelector(`[data-client-id="${CSS.escape(device.id)}"]`);
               const status = row.querySelector('[data-field="status"]');
               status.querySelector('.dot').classList.toggle('online', device.online);
               status.querySelector('span:last-child').textContent = device.online ? 'online' : 'offline';
               for (const [field, value] of Object.entries({hostname: device.hostname, platform: device.platform, groups: device.groups, agent: device.agent_version, last_seen: device.last_seen_text})) {
                  row.querySelector(`[data-field="${field}"]`).textContent = value || '–';
               }
               Object.assign(row.dataset, {status: device.online ? '1' : '0', hostname: device.hostname.toLowerCase(), platform: device.platform,
                  groups: device.groups.toLowerCase(), agent: device.agent_version.toLowerCase(), last_seen: String(device.last_seen), search: JSON.stringify(device).toLowerCase()});
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

   applyView();
   if (table || document.querySelector('#action-table')) {
      updateStatus();
      setInterval(updateStatus, 5000);
   }
})();

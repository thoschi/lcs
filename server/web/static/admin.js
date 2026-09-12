(() => {
   const capability = document.querySelector('#action-capability');
   const parameters = document.querySelector('#action-parameters');
   capability?.addEventListener('change', () => {
      const example = capability.selectedOptions[0].dataset.parameters;
      if (example) parameters.value = JSON.stringify(JSON.parse(example), null, 2);
   });
   const table = document.querySelector('#client-table');
   if (!table) return;
   const body = table.tBodies[0];
   const search = document.querySelector('#client-search');
   const platform = document.querySelector('#platform-filter');
   const deviceType = document.querySelector('#client-type-filter');
   const group = document.querySelector('#group-filter');
   const count = document.querySelector('#client-result-count');
   let sortKey = 'hostname';
   let sortAscending = true;
   const rows = () => [...body.querySelectorAll('tr[data-client-id]')];

   const applyView = () => {
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

   search.addEventListener('input', applyView);
   platform.addEventListener('change', applyView);
   deviceType.addEventListener('change', applyView);
   group.addEventListener('change', applyView);
   document.querySelectorAll('.sort-button').forEach(button => button.addEventListener('click', () => sortRows(button.dataset.sort)));
   document.querySelectorAll('.copy-button').forEach(button => button.addEventListener('click', async () => {
      await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).textContent.trim());
      button.textContent = 'Kopiert';
   }));

   const updateStatus = async () => {
      const indicator = document.querySelector('#client-refresh-state');
      try {
         const response = await fetch('/admin/client-status', {headers: {'Accept': 'application/json'}});
         if (!response.ok) throw new Error(`HTTP ${response.status}`);
         const data = await response.json();
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
         });
         indicator.textContent = `Zuletzt aktualisiert: ${new Date().toLocaleTimeString('de-DE')}`;
         applyView();
      } catch (error) {
         indicator.textContent = 'Statusaktualisierung vorübergehend nicht verfügbar';
      }
   };

   applyView();
   setInterval(updateStatus, 10000);
})();

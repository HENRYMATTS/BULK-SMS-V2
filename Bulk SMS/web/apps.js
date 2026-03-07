// app.js

// -----------------------------------------------------------
// --- 1. PYTHON-TO-JAVASCRIPT EXPOSED HELPERS (LOGGING/STATUS) ---
// -----------------------------------------------------------

// Function to update the main status circle and text
eel.expose(js_update_status);
function js_update_status(color) {
    const indicator = document.getElementById('status-indicator');
    const statusText = document.getElementById('status-text');

    if (indicator) {
        // Use classList for status indicator
        indicator.classList.remove('white', 'red', 'yellow', 'green');
        indicator.classList.add(color);
        
        if (statusText) {
            let text = color.toUpperCase();
            if (color === 'white') text = 'IDLE';
            if (color === 'yellow') text = 'PROCESSING';
            if (color === 'green') text = 'READY';
            if (color === 'red') text = 'CRITICAL';
            statusText.textContent = text;
        }
    }
}

// Function to log a message to a specific HTML element (e.g., 'serial', 'hardware-log')
eel.expose(js_log_update);
function js_log_update(element_id, message) {
    const logArea = document.getElementById(element_id);
    if (logArea) {

         if (logArea.innerHTML.includes('Waiting for')) {
            logArea.innerHTML = '';
        }

        const newEntry = document.createElement('p');
        newEntry.style.margin = "0 0 10px 0"; // Tighten spacing between logs
        // Use innerHTML to allow colored spans passed from Python
        newEntry.innerHTML = message;

        // Add the new entry at the top
        logArea.prepend(newEntry);
        logArea.scrollbottom = logArea.scrollHeight;
    }
}





// CRITICAL FIX: Function called by Python after page load to start the status check handshake
eel.expose(js_trigger_initial_status_check);
function js_trigger_initial_status_check() {
    console.log("JS is ready. Calling Python for initial status check...");
    // Call the exposed Python function (now safe to call)
    eel.check_initial_status()(); 
}

// NEW: Function to update the color of a specific port element (p1 to p8)
eel.expose(js_update_port_color);
function js_update_port_color(port_id, color) {
    const portElement = document.getElementById(port_id);
    if (portElement) {
        // Use class-based styling (white, red, yellow, green)
        portElement.classList.remove('white', 'red', 'yellow', 'green');
        portElement.classList.add(color);
        // Fallback for direct color if classes are not defined in CSS (to ensure visibility):
        portElement.style.backgroundColor = color; 
    }
}

// NEW: Function to update the CONNECT button status
eel.expose(js_update_connect_button);
function js_update_connect_button(is_connected) {
    const button = document.getElementById('connect-button');
    if (!button) return;

    // Reset button state
    button.disabled = false;
    button.classList.remove('secondary', 'success', 'warning', 'danger');
    
    if (is_connected) {
        button.textContent = 'CONNECTED'; // <--- CORRECTED TEXT
        button.classList.add('success');
    } else {
        button.textContent = 'CONNECT';
        button.classList.add('secondary');
    }
}

// Update Airtel indicator (red when connected, gray otherwise)
eel.expose(js_update_airtel_indicator);
function js_update_airtel_indicator(connected) {
    const indicator = document.getElementById('airtel-indicator');
    if (indicator) {
        indicator.style.backgroundColor = connected ? 'red' : 'gray';
    }
}


// -----------------------------------------------------------
// --- 2. UI-SPECIFIC HELPERS (Clear Button) ---
// -----------------------------------------------------------

const clearButton = document.getElementById('clear');
if (clearButton) {
    clearButton.addEventListener('click', function() {
        document.getElementById('serial').innerHTML = '<p>Logs cleared...</p>';
        // document.getElementById('hardware-log').innerHTML = '<p>Waiting for connection...</p>';
        // document.getElementById('save-status-area').innerHTML = '<p>Awaiting number and group entry...</p>';
    });
}


// -----------------------------------------------------------
// --- 3. DB ENTRY LOGIC (Save/Update & Delete) ---
// -----------------------------------------------------------
const saveForm = document.getElementById('save-number-form');

if (saveForm) {
    saveForm.addEventListener('submit', async function(event) {
        event.preventDefault();
        
        const saveButton = document.getElementById('save-update-button');
        saveButton.disabled = true;
        saveButton.textContent = 'Saving...';

        const phoneNumber = document.getElementById('phone-number-input').value.trim();
        const groupName = document.getElementById('group-name-input').value.trim();
        
        const result_text = await eel.save_data_entry(phoneNumber, groupName)();

        // Display the result in the dedicated status area
        let color = result_text.includes('Error') ? 'red' : 'green';
        js_log_update('save-status-area', `<span style="color:${color};">${result_text}</span>`);

        saveButton.disabled = false;
        saveButton.textContent = 'Save / Update';
    });
}


// --- Phone number group lookup popup ---
const phoneInput = document.getElementById('phone-number-input');
const popup = document.getElementById('number-group-popup');
let debounceTimer;

if (phoneInput && popup) {
    phoneInput.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        const number = this.value.trim();
        if (number.length < 5) { // too short, hide popup
            popup.style.display = 'none';
            return;
        }
        debounceTimer = setTimeout(async () => {
            const groups = await eel.get_groups_for_number(number)();
            if (groups.length > 0) {
                // Position popup near the input
                const rect = phoneInput.getBoundingClientRect();
                popup.style.top = (rect.bottom + window.scrollY + 5) + 'px';
                popup.style.left = (rect.left + window.scrollX) + 'px';
                popup.innerHTML = '<strong>Groups:</strong> ' + groups.join(', ');
                popup.style.display = 'block';
            } else {
                popup.style.display = 'none';
            }
        }, 500); // wait 500ms after typing stops
    });

    // Hide popup when input loses focus (optional)
    phoneInput.addEventListener('blur', function() {
        setTimeout(() => { popup.style.display = 'none'; }, 200);
    });
}




const deleteForm = document.getElementById('delete-number-form');

if (deleteForm) {
    deleteForm.addEventListener('submit', async function(event) {
        event.preventDefault();
        
        const deleteButton = document.getElementById('delete-button');
        deleteButton.disabled = true;
        deleteButton.textContent = 'Deleting...';

        const phoneNumber = document.getElementById('delete-number-input').value.trim();
        const logAreaId = 'save-status-area';

        const result_text = await eel.delete_number_entry(phoneNumber)();

        let color = result_text.includes('Error') ? 'red' : 'green';
        js_log_update(logAreaId, `<span style="color:${color};">${result_text}</span>`);

        deleteButton.disabled = false;
        deleteButton.textContent = 'Delete Number';
        document.getElementById('delete-number-input').value = '';
    });
}


// -----------------------------------------------------------
// --- 4. HARDWARE CONNECTION LOGIC (FIXED FOR THREADING) ---
// -----------------------------------------------------------

const connectButton = document.getElementById('connect-button');
const hwLogAreaId = 'hardware-log';

if (connectButton) {
    // CRITICAL FIX: Removed 'async' and 'await' from the handler
    connectButton.addEventListener('click', function(event) {
        event.preventDefault();

        // 1. Immediately update UI to 'Processing' state
        connectButton.disabled = true;
        connectButton.textContent = 'SCANNING...';
        
        // Use PicoCSS classes for styling ('warning' for yellow/in progress)
        connectButton.classList.remove('secondary', 'success');
        connectButton.classList.add('warning'); 

        js_update_status('yellow');
        js_log_update(hwLogAreaId, '<span style="color:yellow;">[INFO] Starting hardware scan in background...</span>');
        
        // 2. CRITICAL: Call the exposed Python function (which starts the THREAD)
        // This call is NON-BLOCKING.
        eel.connect_hardware()(); 
        
        // 3. The function returns immediately, keeping the UI responsive.
    });
}

// -----------------------------------------------------------
// --- 5. BULK SEND DISPATCHER LOGIC ---
// -----------------------------------------------------------
// Targets the form ID: 'group-form'
const sendForm = document.getElementById('group-form');

if (sendForm) {
    sendForm.addEventListener('submit', async function(event) {
        event.preventDefault(); 
        
        // Targets the button ID: 'submit-button'
        const sendButton = document.getElementById('submit-button');
        
        sendButton.disabled = true;
        sendButton.textContent = 'Starting Dispatch...';

        // Targets the inputs: 'input-groups' and 'message-body-input'
        const groupsString = document.getElementById('input-groups').value.trim();
        const messageBody = document.getElementById('message-body-input').value.trim(); 
        
        const logAreaId = 'serial'; 

        // Set the status indicator to yellow for "Processing/Loading"
        js_update_status('yellow'); 
        js_log_update(logAreaId, '<span style="color:yellow;">[INFO] Preparing message queue...</span>');
        
        // Call the exposed Python function (which launches the Python thread)
        const status = await eel.start_bulk_send(groupsString, messageBody)();
        
        // Handle immediate response from Python
        if (status.includes('ERROR')) {
            sendButton.disabled = false;
            sendButton.textContent = 'Send SMS';
            js_log_update(logAreaId, `<span style="color:red;">[ERROR] ${status}</span>`);
            js_update_status('red'); 
        
        } else if (status.includes('Dispatch Started') || status.includes('RECOVERY STARTED')) {
            let color = status.includes('RECOVERY') ? 'red' : 'green';
            js_log_update(logAreaId, `<span style="color:${color};">[SUCCESS] ${status}</span>`);
            // Set indicator to RED to show active/critical job state
            js_update_status('red'); 
            
            sendButton.textContent = 'Sending... (Running in background)';
        }
    });
}




// --- STATS MODAL LOGIC ---

function openStatsModal() {
    document.getElementById('statsModal').style.display = 'block';
    // Clear previous results when opening
    document.getElementById('checkGroupInput').value = '';
    document.getElementById('groupCountResult').innerText = '';
}

function closeStatsModal() {
    document.getElementById('statsModal').style.display = 'none';
}




// --- CAPACITY PLANNER ---

async function checkGroupCount() {
    let inputField = document.getElementById('checkGroupInput');
    let resultDiv = document.getElementById('groupCountResult');
    let groupsStr = inputField.value.trim();

    if (groupsStr === "") {
        resultDiv.innerText = "⚠️ Please enter at least one group name.";
        resultDiv.style.color = "#ef4444";
        return;
    }

    resultDiv.innerText = "⏳ Calculating...";
    resultDiv.style.color = "#94a3b8";

    let response = await eel.check_group_count_py(groupsStr)();

    resultDiv.innerText = "✅ " + response;
    resultDiv.style.color = "#10b981";

    // Auto-fill the Capacity Planner
    let countMatch = response.match(/\d+(,\d+)*/);
    if (countMatch) {
        let numStr = countMatch[0].replace(/,/g, '');
        document.getElementById('calcRecipients').value = numStr;
    }
}


function calculateFramework() {
    let recipients = parseInt(document.getElementById('calcRecipients').value) || 0;
    let message = document.getElementById('calcMessage').value;

    // Count characters (including newlines as one char each)
    let charCount = message.length;

    // Determine segments
    let segments = 1;
    if (charCount > 160) {
        // For messages >160 chars, each segment can hold up to 153 characters (due to header)
        // But for simplicity, we'll use 153 as per typical concatenated SMS.
        // Actually, standard concatenated SMS uses 153 chars per segment for GSM-7.
        // We'll use a while loop to calculate properly.
        let remaining = charCount;
        segments = 0;
        while (remaining > 0) {
            remaining -= 153;
            segments++;
        }
        // Alternatively, a quick formula: segments = Math.ceil(charCount / 153);
    } else {
        segments = 1;
    }

    // For simplicity, we can also just use: segments = Math.ceil(charCount / 153);
    // But we'll keep the loop for clarity.

    let resultDiv = document.getElementById('frameworkResult');

    if (recipients <= 0) {
        resultDiv.style.display = 'block';
        resultDiv.innerHTML = '<span style="color:#ef4444;">Please enter a valid number of recipients.</span>';
        return;
    }

    // Apply Framework Math (12% retry buffer, 270 msg/module, 8% load balance buffer)
    let totalSMS = Math.ceil(recipients * segments * 1.12);
    let modules = Math.ceil(totalSMS / 270);
    if (modules === 0) modules = 1;
    let loadPerSim = Math.ceil((totalSMS / modules) * 1.08);

    resultDiv.style.display = 'block';
    resultDiv.innerHTML = `
        <div style="display:flex; justify-content:space-between; text-align:center; gap:10px;">
            <div style="background:#1e293b; padding:15px; border-radius:8px; flex:1; border-top: 3px solid #10b981;">
                <div style="font-size:0.8em; color:#94a3b8; margin-bottom:5px;">Total SMS (Fuel)</div>
                <div style="font-size:1.8em; font-weight:bold; color:#10b981;">${totalSMS}</div>
                <div style="font-size:0.7em; color:#94a3b8;">${charCount} chars → ${segments} segment(s)</div>
            </div>
            <div style="background:#1e293b; padding:15px; border-radius:8px; flex:1; border-top: 3px solid #f59e0b;">
                <div style="font-size:0.8em; color:#94a3b8; margin-bottom:5px;">Modules Needed</div>
                <div style="font-size:1.8em; font-weight:bold; color:#f59e0b;">${modules}</div>
            </div>
            <div style="background:#1e293b; padding:15px; border-radius:8px; flex:1; border-top: 3px solid #38bdf8;">
                <div style="font-size:0.8em; color:#94a3b8; margin-bottom:5px;">Load per SIM</div>
                <div style="font-size:1.8em; font-weight:bold; color:#38bdf8;">~${loadPerSim}</div>
            </div>
        </div>
    `;
}



// Update stats table
eel.expose(js_update_stats_table);
function js_update_stats_table(data) {
    const container = document.getElementById('stats-table-container');
    const thead = document.getElementById('stats-thead');
    const tbody = document.getElementById('stats-tbody');

    if (!data || Object.keys(data).length === 0) {
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';

    // Build header row
    let modems = Object.keys(data).sort();
    let headerRow = '<tr><th>Metric</th>';
    modems.forEach(m => headerRow += `<th>${m}</th>`);
    headerRow += '</tr>';
    thead.innerHTML = headerRow;

    // Metrics to display
    const metrics = [
        { key: 'sent', label: 'Sent' },
        { key: 'failed', label: 'Failed (Perm)' },
        { key: 'retry_attempts', label: 'Retry Attempts' },
        { key: 'first_try_success', label: 'First-Try Success' },
        { key: 'retry_success', label: 'Retry Success' },
        { key: 'timeout_count', label: 'Timeouts' },
        { key: 'reset_count', label: 'Resets' },
        { key: 'network_registered', label: 'Network' },
        { key: 'signal', label: 'Signal' },
        { key: 'last_error', label: 'Last Error' }
    ];

    let tbodyHtml = '';
    metrics.forEach(metric => {
        let row = `<tr><td>${metric.label}</td>`;
        modems.forEach(modem => {
            let value = data[modem][metric.key];
            if (value === undefined) value = '';
            if (metric.key === 'network_registered') value = value ? 'Yes' : 'No';
            if (metric.key === 'signal' && value === null) value = '';
            row += `<td>${value}</td>`;
        });
        row += '</tr>';
        tbodyHtml += row;
    });
    tbody.innerHTML = tbodyHtml;
}

// Stop button handler
const stopButton = document.getElementById('stop-button');
if (stopButton) {
    stopButton.addEventListener('click', async function() {
        stopButton.disabled = true;
        stopButton.textContent = 'Stopping...';
        const result = await eel.stop_sending()();
        stopButton.disabled = false;
        stopButton.textContent = 'Stop Sending';
    });
}


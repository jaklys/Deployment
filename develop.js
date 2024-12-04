function addExportButton() {
    const reloadButton = document.querySelector('input[value="Reload"]').parentNode;
    const buttonColumn = document.createElement('div');
    buttonColumn.className = 'column';
    buttonColumn.innerHTML = `
        <button class="button is-dark is-fullwidth" id="exportTableButton">
            Export JSON
        </button>
    `;
    reloadButton.parentNode.insertBefore(buttonColumn, reloadButton.nextSibling);
 }
 
 function exportTableToJson() {
    const table = document.getElementById('myTable');
    const tableData = [];
    const headers = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
 
    table.querySelectorAll('tbody tr').forEach(row => {
        if (row.style.display !== 'none') {  // Only export visible rows
            const rowData = {};
            row.querySelectorAll('td').forEach((cell, index) => {
                rowData[headers[index]] = cell.textContent.trim();
            });
            tableData.push(rowData);
        }
    });
 
    const jsonStr = JSON.stringify(tableData, null, 2);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = 'table_data.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
 }
 
 document.addEventListener('DOMContentLoaded', () => {
    addExportButton();
    document.getElementById('exportTableButton').addEventListener('click', exportTableToJson);
 });

 <footer class="footer">
    <div class="content has-text-centered">
        <div class="buttons is-centered">
            <button class="button is-dark" id="exportTableButton">
                Export JSON
            </button>
            <!-- Existing footer content -->
        </div>
        <p><strong>Started at </strong><%= FormatDate(startTime, tzOffset) %></p>
        <p>Powered by Fiat Devops</p>
    </div>
</footer>



<script>
if (localStorage.getItem('darkMode') === 'true') {
    document.documentElement.setAttribute('data-theme', 'dark');
}
</script>
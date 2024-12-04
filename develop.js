function myFunction() {
    const searchInput = document.getElementById('myInput');
    searchInput.addEventListener('input', function() {
        searchInput.className = "input is-primary is-rounded";
        const searchValue = this.value;
        try {
            const regex = new RegExp(searchValue, 'i');
            const table = document.getElementById('myTable');
            const headerRow = table.getElementsByTagName('thead')[0];
            const tableBody = table.getElementsByTagName('tbody')[0];
            
            if (tableBody) {
                const rows = tableBody.getElementsByTagName('tr');
                Array.from(rows).forEach(row => {
                    const cellsText = Array.from(row.cells).map(cell => cell.textContent).join(' ');
                    row.style.display = regex.test(cellsText) ? '' : 'none';
                });
            }
        } catch(e) {
            searchInput.className = "input is-danger is-rounded";
        }
    });
}
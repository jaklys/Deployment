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

<!-- Add to head -->
<link href="https://cdn.jsdelivr.net/npm/bulma-prefers-dark@0.1.0-beta.1/css/bulma-prefers-dark.min.css" rel="stylesheet">

<!-- Add button to columns -->
<div class="column">
    <button class="button is-dark is-fullwidth" id="toggleDarkMode">
        <span class="icon">
            <i class="fas fa-moon"></i>
        </span>
        <span>Dark Mode</span>
    </button>
</div>

<!-- Add JavaScript -->
<script>
function initDarkMode() {
    const darkModeBtn = document.getElementById('toggleDarkMode');
    const html = document.documentElement;
    
    const isDark = localStorage.getItem('darkMode') === 'true';
    if (isDark) {
        html.setAttribute('data-theme', 'dark');
        darkModeBtn.classList.add('is-light');
        darkModeBtn.classList.remove('is-dark');
    }

    darkModeBtn.addEventListener('click', () => {
        const isDark = html.getAttribute('data-theme') === 'dark';
        html.setAttribute('data-theme', isDark ? 'light' : 'dark');
        localStorage.setItem('darkMode', !isDark);
        darkModeBtn.classList.toggle('is-light', !isDark);
        darkModeBtn.classList.toggle('is-dark', isDark);
    });
}

document.addEventListener('DOMContentLoaded', initDarkMode);
</script>
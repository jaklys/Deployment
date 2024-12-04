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

<!-- Add switch styles -->
<style>
.switch-wrapper {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.switch[type='checkbox'] {
    height: 0;
    width: 0;
    visibility: hidden;
}

.switch[type='checkbox'] + label {
    cursor: pointer;
    width: 3rem;
    height: 1.5rem;
    background: #ddd;
    display: block;
    border-radius: 1.5rem;
    position: relative;
}

.switch[type='checkbox'] + label:after {
    content: '';
    position: absolute;
    top: 0.125rem;
    left: 0.125rem;
    width: 1.25rem;
    height: 1.25rem;
    background: #fff;
    border-radius: 1.25rem;
    transition: 0.3s;
}

.switch[type='checkbox']:checked + label {
    background: #363636;
}

.switch[type='checkbox']:checked + label:after {
    left: calc(100% - 0.125rem);
    transform: translateX(-100%);
}
</style>

<!-- Replace button with switch -->
<div class="column">
    <div class="switch-wrapper">
        <input type="checkbox" id="toggleDarkMode" class="switch">
        <label for="toggleDarkMode"></label>
        <span class="icon">
            <i class="fas fa-moon"></i>
        </span>
    </div>
</div>

<!-- Updated JavaScript -->
<script>
function initDarkMode() {
    const darkModeSwitch = document.getElementById('toggleDarkMode');
    const html = document.documentElement;
    
    darkModeSwitch.checked = localStorage.getItem('darkMode') === 'true';
    if (darkModeSwitch.checked) {
        html.setAttribute('data-theme', 'dark');
    }

    darkModeSwitch.addEventListener('change', () => {
        html.setAttribute('data-theme', darkModeSwitch.checked ? 'dark' : 'light');
        localStorage.setItem('darkMode', darkModeSwitch.checked);
    });
}

document.addEventListener('DOMContentLoaded', initDarkMode);
</script>
import os
import json

def list_files_in_directory(directory_path):
    files_list = []

    try:
        # Získání seznamu souborů a složek v zadané složce
        files = os.listdir(directory_path)

        # Iterace přes soubory a složky
        for file in files:
            full_path = os.path.join(directory_path, file)
            # Kontrola, zda jde o soubor
            if os.path.isfile(full_path):
                files_list.append(file)  # Přidá název souboru do seznamu

        # Vytisknutí seznamu souborů v JSON formátu
        print(json.dumps(files_list, indent=4))

    except FileNotFoundError:
        print(f"Složka {directory_path} neexistuje.")
    except PermissionError:
        print(f"Nemáte oprávnění ke čtení složky {directory_path}.")

# Příklad použití:
directory_path = "/cesta/k/složce"
list_files_in_directory(directory_path)
// Clipper als Mac-App: zeigt die Uebersichtsseite aus docs/ in einem eigenen Fenster.
// Die Daten laedt die Seite live aus dem GitHub-Repository; Links oeffnen im Standardbrowser.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("Clipper konnte nicht gestartet werden");
}

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![greet])
        .on_window_event(|_window, event| {
            // T3.5.1: When user closes the Tauri window after completing onboarding
            // (clicked "Open Portal" in done.js → window closes), check is_complete.
            // If true, remove ONLY the autostart .desktop. The persistent launcher
            // in ~/.local/share/applications/ stays so user can re-launch for
            // credential management (manage mode).
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let state: serde_json::Value = reqwest::blocking::get("http://localhost:9091/onboarding/state")
                    .and_then(|r| r.json())
                    .unwrap_or(serde_json::json!({"is_complete": false}));
                if state["is_complete"].as_bool().unwrap_or(false) {
                    let autostart = dirs::config_dir()
                        .map(|d| d.join("autostart").join("blackbox-setup.desktop"));
                    if let Some(p) = autostart {
                        match std::fs::remove_file(&p) {
                            Ok(_) => eprintln!("[blackbox-setup] removed autostart entry at {}; persistent launcher in ~/.local/share/applications/ remains for re-launch", p.display()),
                            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                                // First run on a dev box may have no autostart yet — that's fine.
                                eprintln!("[blackbox-setup] no autostart entry to remove (already absent or never installed)");
                            }
                            Err(e) => eprintln!("[blackbox-setup] failed to remove autostart entry: {e}"),
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

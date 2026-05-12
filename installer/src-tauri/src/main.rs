// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn wait_for_server(url: &str, timeout_secs: u64) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed().as_secs() < timeout_secs {
        if reqwest::blocking::get(url).is_ok() {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    false
}

fn main() {
    if !wait_for_server("http://localhost:9091/health", 180) {
        eprintln!("Orchestrator failed to come up within 180s — aborting blackbox-setup launch");
        std::process::exit(1);
    }
    eprintln!("[blackbox-setup] BlackBox Orchestrator is healthy; launching window");
    installer_lib::run()
}

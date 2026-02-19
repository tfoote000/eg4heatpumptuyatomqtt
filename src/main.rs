mod config;
mod mqtt;
mod tuya;

use std::collections::HashMap;
use std::time::Duration;

use tokio::sync::mpsc;
use tracing::{error, info, warn};

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let config = match config::Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            error!("Configuration error: {}", e);
            std::process::exit(1);
        }
    };

    info!(
        "Starting tuya-to-mqtt bridge (mqtt={}:{}, devices={})",
        config.mqtt.broker_host,
        config.mqtt.broker_port,
        config.devices.len(),
    );

    for device in &config.devices {
        info!(
            "  Device: {} ({}) at {} — {} DPs mapped",
            device.name,
            device.id,
            device.ip,
            device.dp_mapping.len(),
        );
    }

    // Channels
    let (mqtt_cmd_tx, mut mqtt_cmd_rx) = mpsc::channel::<mqtt::client::MqttMessage>(100);
    let (dp_update_tx, dp_update_rx) = mpsc::channel::<tuya::DpUpdate>(200);

    // Create MQTT client and spawn event loop (handles both MQTT I/O and DP publishing)
    let mqtt_client = mqtt::client::MqttClient::new(&config);
    let mqtt_handle = tokio::spawn(async move {
        mqtt_client.run(mqtt_cmd_tx, dp_update_rx).await;
    });

    // Per-device channels: keyed by topic_name for command routing
    let mut device_cmd_txs: HashMap<String, mpsc::Sender<tuya::DpCommand>> = HashMap::new();

    // Spawn a Tuya client task for each device
    let poll_interval = Duration::from_secs(config.tuya.poll_interval_secs);
    let mut device_handles = Vec::new();

    for device_config in &config.devices {
        let (cmd_tx, cmd_rx) = mpsc::channel::<tuya::DpCommand>(50);
        device_cmd_txs.insert(device_config.topic_name.clone(), cmd_tx);

        let client = tuya::client::TuyaClient::new(device_config.clone());
        let dp_tx = dp_update_tx.clone();

        let handle = tokio::spawn(async move {
            client.run(dp_tx, cmd_rx, poll_interval).await;
        });
        device_handles.push(handle);
    }

    // Drop the original sender so the channel closes when all device tasks finish
    drop(dp_update_tx);

    // Build device lookup for command routing (keyed by topic_name)
    let device_configs: HashMap<String, config::DeviceConfig> = config
        .devices
        .iter()
        .map(|d| (d.topic_name.clone(), d.clone()))
        .collect();
    let topic_prefix = config.mqtt.topic_prefix.clone();

    // Main loop: route MQTT commands to devices + handle shutdown
    loop {
        tokio::select! {
            Some(msg) = mqtt_cmd_rx.recv() => {
                // Parse topic: {prefix}/{topic_name}/command/{dp_code}
                if let Some((topic_name, dp_code)) = parse_command_topic(&msg.topic, &topic_prefix) {
                    if let Some(device_config) = device_configs.get(topic_name) {
                        if let Some(cmd) = tuya::client::build_command(device_config, dp_code, &msg.payload) {
                            if let Some(cmd_tx) = device_cmd_txs.get(topic_name) {
                                if cmd_tx.send(cmd).await.is_err() {
                                    warn!("Command channel closed for device {}", topic_name);
                                }
                            }
                        } else {
                            warn!("Could not build command: dp_code={}, value={}", dp_code, msg.payload);
                        }
                    } else {
                        warn!("Unknown device in command topic: {}", topic_name);
                    }
                }
            }
            _ = tokio::signal::ctrl_c() => {
                info!("Received SIGINT, shutting down");
                break;
            }
            _ = async {
                let mut sigterm = tokio::signal::unix::signal(
                    tokio::signal::unix::SignalKind::terminate()
                ).expect("Failed to register SIGTERM handler");
                sigterm.recv().await;
            } => {
                info!("Received SIGTERM, shutting down");
                break;
            }
        }
    }

    // Cleanup
    for handle in device_handles {
        handle.abort();
    }
    mqtt_handle.abort();
    info!("tuya-to-mqtt bridge stopped");
}

/// Parse a command topic into (topic_name, dp_code).
/// Expected format: {prefix}/{topic_name}/command/{dp_code}
fn parse_command_topic<'a>(topic: &'a str, prefix: &str) -> Option<(&'a str, &'a str)> {
    let rest = topic.strip_prefix(prefix)?.strip_prefix('/')?;
    // rest = "{topic_name}/command/{dp_code}"
    let (topic_name, rest) = rest.split_once('/')?;
    let dp_code = rest.strip_prefix("command/")?;
    if topic_name.is_empty() || dp_code.is_empty() {
        return None;
    }
    Some((topic_name, dp_code))
}

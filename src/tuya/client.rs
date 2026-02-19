use std::time::Duration;

use rust_async_tuyapi::mesparse::CommandType;
use rust_async_tuyapi::tuyadevice::TuyaDevice;
use rust_async_tuyapi::{Payload, PayloadStruct};
use serde_json::json;
use tokio::sync::mpsc;
use tracing::{debug, error, info, warn};

use crate::config::DeviceConfig;

use super::{DpCommand, DpUpdate};

/// Convert HA enum values to Tuya device values (command direction).
fn ha_to_tuya(dp_code: &str, value: &str) -> String {
    match dp_code {
        "mode" => match value {
            "cool" => "cold",
            "heat" => "hot",
            "fan_only" => "wind",
            _ => value,
        },
        "fan_speed_enum" => match value {
            "medium" => "mid",
            _ => value,
        },
        _ => value,
    }
    .to_string()
}

/// Convert Tuya device values to HA enum values (state direction).
fn tuya_to_ha(dp_code: &str, value: &str) -> String {
    match dp_code {
        "mode" => match value {
            "cold" => "cool",
            "hot" => "heat",
            "wind" => "fan_only",
            _ => value,
        },
        "fan_speed_enum" => match value {
            "mid" => "medium",
            _ => value,
        },
        _ => value,
    }
    .to_string()
}

pub struct TuyaClient {
    config: DeviceConfig,
}

impl TuyaClient {
    pub fn new(config: DeviceConfig) -> Self {
        Self { config }
    }

    /// Main device loop. Connects, polls, handles commands, reconnects on failure.
    pub async fn run(
        &self,
        dp_tx: mpsc::Sender<DpUpdate>,
        mut cmd_rx: mpsc::Receiver<DpCommand>,
        poll_interval: Duration,
    ) {
        let mut backoff = Duration::from_secs(5);
        let max_backoff = Duration::from_secs(60);

        loop {
            info!(
                "Connecting to device {} ({}) at {}",
                self.config.name, self.config.id, self.config.ip
            );

            match self.run_session(&dp_tx, &mut cmd_rx, poll_interval).await {
                Ok(()) => {
                    info!("Device {} session ended cleanly", self.config.name);
                    backoff = Duration::from_secs(5);
                }
                Err(e) => {
                    error!(
                        "Device {} session error: {}. Reconnecting in {:?}",
                        self.config.name, e, backoff
                    );
                    tokio::time::sleep(backoff).await;
                    backoff = (backoff * 2).min(max_backoff);
                }
            }
        }
    }

    async fn run_session(
        &self,
        dp_tx: &mpsc::Sender<DpUpdate>,
        cmd_rx: &mut mpsc::Receiver<DpCommand>,
        poll_interval: Duration,
    ) -> Result<(), String> {
        let mut device = TuyaDevice::new("3.3", &self.config.id, Some(&self.config.key), self.config.ip)
            .map_err(|e| format!("Failed to create device: {e:?}"))?;

        let mut receiver = device
            .connect()
            .await
            .map_err(|e| format!("Failed to connect: {e:?}"))?;

        info!("Connected to device {}", self.config.name);

        // Initial DP query
        self.query_all_dps(&mut device).await?;

        let mut heartbeat_interval = tokio::time::interval(Duration::from_secs(10));
        let mut poll_timer = tokio::time::interval(poll_interval);
        // Skip first tick (we already queried)
        poll_timer.tick().await;

        loop {
            tokio::select! {
                _ = heartbeat_interval.tick() => {
                    device.heartbeat().await
                        .map_err(|e| format!("Heartbeat failed: {e:?}"))?;
                }
                _ = poll_timer.tick() => {
                    self.query_all_dps(&mut device).await?;
                }
                msg = receiver.recv() => {
                    match msg {
                        Some(Ok(messages)) => {
                            for m in messages {
                                if m.command == Some(CommandType::HeartBeat) {
                                    continue;
                                }
                                self.process_message(&m, dp_tx).await;
                            }
                        }
                        Some(Err(e)) => {
                            return Err(format!("Device error: {e:?}"));
                        }
                        None => {
                            return Err("Device channel closed".into());
                        }
                    }
                }
                Some(cmd) = cmd_rx.recv() => {
                    info!("Sending command to {}: {}", self.config.name, cmd.dps);
                    if let Err(e) = device.set_values(cmd.dps.clone()).await {
                        warn!("Failed to send command to {}: {:?}", self.config.name, e);
                    }
                }
            }
        }
    }

    async fn query_all_dps(&self, device: &mut TuyaDevice) -> Result<(), String> {
        let payload = Payload::Struct(PayloadStruct {
            dev_id: self.config.id.clone(),
            gw_id: Some(self.config.id.clone()),
            uid: None,
            t: None,
            dp_id: None,
            dps: Some(json!({})),
        });

        device
            .get(payload)
            .await
            .map_err(|e| format!("DP query failed: {e:?}"))
    }

    async fn process_message(
        &self,
        msg: &rust_async_tuyapi::mesparse::Message,
        dp_tx: &mpsc::Sender<DpUpdate>,
    ) {
        // Extract dps from whichever payload variant the library returns.
        // rust-async-tuyapi sometimes returns DP query responses as Payload::String
        // containing JSON like {"dps":{"1":true,"2":21,...}} instead of Payload::Struct.
        let dps_value: Option<serde_json::Value> = match &msg.payload {
            Payload::Struct(ps) => {
                debug!("PayloadStruct: dev_id={}, dps={:?}", ps.dev_id, ps.dps);
                ps.dps.clone()
            }
            Payload::String(s) => {
                debug!("Payload::String, attempting JSON parse");
                serde_json::from_str::<serde_json::Value>(s)
                    .ok()
                    .and_then(|v| v.get("dps").cloned())
            }
            Payload::Raw(b) => {
                debug!("Payload::Raw ({} bytes), skipping", b.len());
                None
            }
            _ => None,
        };

        let Some(dps) = dps_value else {
            debug!("No dps in message, skipping");
            return;
        };
        let Some(dps_map) = dps.as_object() else {
            debug!("dps is not a JSON object: {}", dps);
            return;
        };

        info!("Processing {} DPs from device", dps_map.len());

        for (dp_id, value) in dps_map {
            let dp_code = self
                .config
                .dp_mapping
                .get(dp_id)
                .map(|info| info.code.as_str())
                .unwrap_or(dp_id);

            let value_str = match value {
                serde_json::Value::Bool(b) => b.to_string(),
                serde_json::Value::Number(n) => n.to_string(),
                serde_json::Value::String(s) => tuya_to_ha(dp_code, s),
                other => other.to_string(),
            };

            debug!("DP {}: {} = {}", dp_id, dp_code, value_str);

            let update = DpUpdate {
                topic_name: self.config.topic_name.clone(),
                dp_code: dp_code.to_string(),
                value: value_str,
            };

            if dp_tx.send(update).await.is_err() {
                warn!("DP update channel closed");
                return;
            }
        }
    }
}

/// Build a DpCommand from a dp_code + string value, using the device's mapping.
pub fn build_command(
    config: &DeviceConfig,
    dp_code: &str,
    raw_value: &str,
) -> Option<DpCommand> {
    let dp_id = config.reverse_mapping.get(dp_code)?;
    let dp_info = config.dp_mapping.get(dp_id)?;

    // Convert HA enum values to Tuya values (e.g. "cool" → "cold", "medium" → "mid")
    let converted_value = ha_to_tuya(dp_code, raw_value);

    // HA sends "off" to command/mode to turn the unit off, and a real mode to turn it on.
    if dp_code == "mode" {
        if let Some(switch_dp_id) = config.reverse_mapping.get("switch") {
            let mut dps = serde_json::Map::new();

            if raw_value == "off" {
                dps.insert(switch_dp_id.clone(), json!(false));
            } else {
                dps.insert(switch_dp_id.clone(), json!(true));
                dps.insert(dp_id.clone(), json!(converted_value));
            }

            return Some(DpCommand {
                dps: serde_json::Value::Object(dps),
            });
        }
    }

    let value: serde_json::Value = match &dp_info.dp_type {
        crate::config::DpType::Boolean => match raw_value {
            "true" | "1" | "on" => json!(true),
            "false" | "0" | "off" => json!(false),
            _ => {
                warn!("Invalid boolean value for {}: {}", dp_code, raw_value);
                return None;
            }
        },
        crate::config::DpType::Integer => {
            let n: i64 = raw_value.parse().or_else(|_| {
                raw_value.parse::<f64>().map(|f| f as i64)
            }).ok()?;
            json!(n)
        }
        crate::config::DpType::Enum(range) => {
            if !range.is_empty() && !range.contains(&converted_value) {
                warn!(
                    "Value '{}' not in declared enum range {:?} for {} — sending anyway",
                    converted_value, range, dp_code
                );
            }
            json!(converted_value)
        }
        crate::config::DpType::Bitmap => {
            warn!("Bitmap commands not supported for {}", dp_code);
            return None;
        }
    };

    let mut dps = serde_json::Map::new();
    dps.insert(dp_id.clone(), value);

    Some(DpCommand {
        dps: serde_json::Value::Object(dps),
    })
}

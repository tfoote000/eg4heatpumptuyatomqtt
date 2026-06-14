//! Home Assistant MQTT discovery.
//!
//! Publishes a retained `climate` discovery config per device so Home
//! Assistant auto-creates the heat-pump entity, replacing the hand-written
//! `climate:` YAML. All topics are built from the runtime MQTT topic prefix
//! and the device's sanitized topic name, so they track whatever
//! `MQTT_TOPIC_PREFIX` the deployment sets.

use serde_json::{json, Value};

use crate::config::{Config, DeviceConfig};

/// One retained discovery message to publish.
pub struct DiscoveryMessage {
    pub topic: String,
    pub payload: String,
}

/// DP codes the climate entity depends on. A device missing any of these is
/// not a heat pump we can model, so we skip discovery for it.
const REQUIRED_CODES: &[&str] = &[
    "work_status",
    "temp_current_f",
    "fan_speed_enum",
    "mode",
    "switch",
    "temp_set_f",
];

/// Build the climate discovery message for a single device, or `None` if the
/// device lacks the DP codes the climate entity needs.
pub fn build_climate_message(
    prefix: &str,
    discovery_prefix: &str,
    device: &DeviceConfig,
) -> Option<DiscoveryMessage> {
    if !REQUIRED_CODES
        .iter()
        .all(|code| device.reverse_mapping.contains_key(*code))
    {
        return None;
    }

    let tn = &device.topic_name;
    let state = |code: &str| format!("{prefix}/{tn}/state/{code}");
    let command = |code: &str| format!("{prefix}/{tn}/command/{code}");

    // Faithful translation of the previous manual `climate:` YAML, plus a
    // device block and unique_id so HA groups it and remembers customizations.
    let payload: Value = json!({
        "name": device.name,
        "unique_id": format!("tuya_{tn}_climate"),
        "action_topic": state("work_status"),
        "availability_topic": format!("{prefix}/{tn}/bridge_status"),
        "payload_available": "online",
        "payload_not_available": "offline",
        "current_temperature_topic": state("temp_current_f"),
        "fan_mode_command_topic": command("fan_speed_enum"),
        "fan_mode_state_topic": state("fan_speed_enum"),
        "fan_modes": ["auto", "low", "medium", "high"],
        "max_temp": 80,
        "min_temp": 65,
        "mode_command_topic": command("mode"),
        "mode_state_topic": state("mode"),
        "modes": ["off", "cool", "heat", "fan_only"],
        "optimistic": true,
        "payload_off": "false",
        "payload_on": "true",
        "power_command_topic": command("switch"),
        "temperature_command_topic": command("temp_set_f"),
        "temperature_unit": "F",
        "device": {
            "identifiers": [format!("tuya_{tn}")],
            "name": device.name,
            "manufacturer": "EG4",
            "model": "Heat Pump",
        },
    });

    Some(DiscoveryMessage {
        topic: format!("{discovery_prefix}/climate/{tn}/config"),
        payload: payload.to_string(),
    })
}

/// Build discovery messages for every device in the config.
pub fn build_messages(config: &Config) -> Vec<DiscoveryMessage> {
    config
        .devices
        .iter()
        .filter_map(|d| {
            build_climate_message(&config.mqtt.topic_prefix, &config.ha.discovery_prefix, d)
        })
        .collect()
}

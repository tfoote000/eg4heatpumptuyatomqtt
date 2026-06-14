//! Home Assistant MQTT discovery.
//!
//! Publishes retained discovery configs per device on connect so Home
//! Assistant auto-creates the entities, replacing the hand-written YAML:
//!   * one `climate` entity (the heat pump itself), and
//!   * a set of `sensor` entities for the solar/grid power figures.
//!
//! All entities are grouped under one HA device. Topics are built from the
//! runtime MQTT topic prefix and the device's sanitized topic name, so they
//! track whatever `MQTT_TOPIC_PREFIX` the deployment sets.

use serde_json::{json, Value};

use crate::config::{Config, DeviceConfig};

/// One retained discovery message to publish.
pub struct DiscoveryMessage {
    pub topic: String,
    pub payload: String,
}

/// DP codes the climate entity depends on. A device missing any of these is
/// not a heat pump we can model, so we skip the climate entity for it.
const REQUIRED_CLIMATE_CODES: &[&str] = &[
    "work_status",
    "temp_current_f",
    "fan_speed_enum",
    "mode",
    "switch",
    "temp_set_f",
];

/// A plain `sensor` entity built from a single state DP code.
struct SensorDef {
    code: &'static str,
    name_suffix: &'static str,
    device_class: Option<&'static str>,
    unit: Option<&'static str>,
    state_class: Option<&'static str>,
    icon: Option<&'static str>,
}

/// The solar/grid sensors carried over from the hand-written `sensor:` YAML.
const SENSORS: &[SensorDef] = &[
    SensorDef {
        code: "solar_power",
        name_suffix: "Solar Power",
        device_class: Some("power"),
        unit: Some("W"),
        state_class: Some("measurement"),
        icon: None,
    },
    SensorDef {
        code: "solar_energy",
        name_suffix: "Solar Energy",
        device_class: Some("energy"),
        unit: Some("Wh"),
        state_class: Some("total_increasing"),
        icon: None,
    },
    SensorDef {
        code: "solar_percent",
        name_suffix: "Solar Percent",
        device_class: None,
        unit: Some("%"),
        state_class: Some("measurement"),
        icon: Some("mdi:solar-power-variant"),
    },
    SensorDef {
        code: "grid_power",
        name_suffix: "Grid Power",
        device_class: Some("power"),
        unit: Some("W"),
        state_class: Some("measurement"),
        icon: None,
    },
    SensorDef {
        code: "grid_percent",
        name_suffix: "Grid Percent",
        device_class: None,
        unit: Some("%"),
        state_class: Some("measurement"),
        icon: Some("mdi:transmission-tower"),
    },
    SensorDef {
        code: "total_energy",
        name_suffix: "Total Energy",
        device_class: Some("energy"),
        unit: Some("Wh"),
        state_class: Some("total_increasing"),
        icon: None,
    },
];

/// The HA `device` block shared by every entity belonging to one device, so
/// HA groups the climate entity and all sensors together.
fn device_block(device: &DeviceConfig) -> Value {
    json!({
        "identifiers": [format!("tuya_{}", device.topic_name)],
        "name": device.name,
        "manufacturer": "EG4",
        "model": "Heat Pump",
    })
}

/// Build the climate discovery message for a single device, or `None` if the
/// device lacks the DP codes the climate entity needs.
fn climate_message(prefix: &str, discovery_prefix: &str, device: &DeviceConfig) -> Option<DiscoveryMessage> {
    if !REQUIRED_CLIMATE_CODES
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
        "device": device_block(device),
    });

    Some(DiscoveryMessage {
        topic: format!("{discovery_prefix}/climate/{tn}/config"),
        payload: payload.to_string(),
    })
}

/// Build a sensor discovery message, or `None` if the device doesn't expose
/// that DP code.
fn sensor_message(
    prefix: &str,
    discovery_prefix: &str,
    device: &DeviceConfig,
    def: &SensorDef,
) -> Option<DiscoveryMessage> {
    if !device.reverse_mapping.contains_key(def.code) {
        return None;
    }

    let tn = &device.topic_name;
    let mut payload = json!({
        "name": format!("{} - {}", device.name, def.name_suffix),
        "unique_id": format!("{tn}_{}", def.code),
        "state_topic": format!("{prefix}/{tn}/state/{}", def.code),
        "availability_topic": format!("{prefix}/{tn}/bridge_status"),
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_block(device),
    });
    let map = payload.as_object_mut().expect("payload is an object");
    if let Some(dc) = def.device_class {
        map.insert("device_class".into(), json!(dc));
    }
    if let Some(u) = def.unit {
        map.insert("unit_of_measurement".into(), json!(u));
    }
    if let Some(sc) = def.state_class {
        map.insert("state_class".into(), json!(sc));
    }
    if let Some(ic) = def.icon {
        map.insert("icon".into(), json!(ic));
    }

    Some(DiscoveryMessage {
        topic: format!("{discovery_prefix}/sensor/{tn}/{}/config", def.code),
        payload: payload.to_string(),
    })
}

/// Build all discovery messages (climate + sensors) for every device.
pub fn build_messages(config: &Config) -> Vec<DiscoveryMessage> {
    let prefix = &config.mqtt.topic_prefix;
    let dp = &config.ha.discovery_prefix;
    let mut messages = Vec::new();

    for device in &config.devices {
        if let Some(msg) = climate_message(prefix, dp, device) {
            messages.push(msg);
        }
        for def in SENSORS {
            if let Some(msg) = sensor_message(prefix, dp, device, def) {
                messages.push(msg);
            }
        }
    }

    messages
}

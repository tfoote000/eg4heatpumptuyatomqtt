use serde::Deserialize;
use std::collections::HashMap;
use std::env;
use std::net::IpAddr;

#[derive(Debug, Clone)]
pub struct Config {
    pub mqtt: MqttConfig,
    pub tuya: TuyaConfig,
    pub devices: Vec<DeviceConfig>,
}

#[derive(Debug, Clone)]
pub struct MqttConfig {
    pub broker_host: String,
    pub broker_port: u16,
    pub username: Option<String>,
    pub password: Option<String>,
    pub topic_prefix: String,
    pub client_id: String,
}

#[derive(Debug, Clone)]
pub struct TuyaConfig {
    pub poll_interval_secs: u64,
}

#[derive(Debug, Clone)]
pub struct DeviceConfig {
    pub id: String,
    pub key: String,
    pub ip: IpAddr,
    pub name: String,
    /// Sanitized name for use in MQTT topics (lowercase, spaces to underscores)
    pub topic_name: String,
    pub dp_mapping: HashMap<String, DpInfo>,
    pub reverse_mapping: HashMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct DpInfo {
    pub code: String,
    pub dp_type: DpType,
}

#[derive(Debug, Clone)]
pub enum DpType {
    Boolean,
    Integer,
    Enum(Vec<String>),
    Bitmap,
}

// Serde structs for parsing tinytuya device listing JSON
#[derive(Deserialize)]
struct RawDevice {
    id: String,
    key: String,
    #[serde(default)]
    ip: Option<String>,
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    mapping: Option<HashMap<String, RawDpMapping>>,
}

#[derive(Deserialize)]
struct RawDpMapping {
    code: String,
    #[serde(rename = "type")]
    dp_type: String,
    #[serde(default)]
    values: Option<serde_json::Value>,
}

fn env_required(key: &str) -> Result<String, String> {
    env::var(key).map_err(|_| format!("{key} environment variable is required"))
}

fn env_optional(key: &str) -> Option<String> {
    env::var(key).ok().filter(|v| !v.is_empty())
}

fn env_or_default<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        let devices_file = env_or_default("DEVICES_FILE", "devices.json".to_string());
        let devices = load_devices(&devices_file)?;

        let config = Self {
            mqtt: MqttConfig {
                broker_host: env_required("MQTT_BROKER_HOST")?,
                broker_port: env_or_default("MQTT_BROKER_PORT", 1883),
                username: env_optional("MQTT_USERNAME"),
                password: env_optional("MQTT_PASSWORD"),
                topic_prefix: env_or_default("MQTT_TOPIC_PREFIX", "tuya".to_string()),
                client_id: env_or_default("MQTT_CLIENT_ID", "tuya-to-mqtt".to_string()),
            },
            tuya: TuyaConfig {
                poll_interval_secs: env_or_default("TUYA_POLL_INTERVAL_SECS", 30),
            },
            devices,
        };
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> Result<(), String> {
        if self.mqtt.broker_host.is_empty() {
            return Err("MQTT_BROKER_HOST must not be empty".into());
        }
        if self.devices.is_empty() {
            return Err("No devices found in devices file".into());
        }
        if self.tuya.poll_interval_secs == 0 {
            return Err("TUYA_POLL_INTERVAL_SECS must be > 0".into());
        }
        Ok(())
    }

    pub fn device_status_topic(&self, topic_name: &str) -> String {
        format!("{}/{}/bridge_status", self.mqtt.topic_prefix, topic_name)
    }

    pub fn device_command_topic(&self, topic_name: &str) -> String {
        format!("{}/{}/command/#", self.mqtt.topic_prefix, topic_name)
    }
}

fn load_devices(path: &str) -> Result<Vec<DeviceConfig>, String> {
    let content =
        std::fs::read_to_string(path).map_err(|e| format!("Failed to read {path}: {e}"))?;

    let raw_devices: Vec<RawDevice> =
        serde_json::from_str(&content).map_err(|e| format!("Failed to parse {path}: {e}"))?;

    raw_devices
        .into_iter()
        .map(|raw| {
            let ip: IpAddr = raw
                .ip
                .as_deref()
                .ok_or_else(|| format!("Device {} missing 'ip' field", raw.id))?
                .parse()
                .map_err(|e| format!("Device {} invalid IP: {e}", raw.id))?;

            let mut dp_mapping = HashMap::new();
            let mut reverse_mapping = HashMap::new();

            if let Some(mapping) = raw.mapping {
                for (dp_id, raw_dp) in mapping {
                    let dp_type = parse_dp_type(&raw_dp);
                    reverse_mapping.insert(raw_dp.code.clone(), dp_id.clone());
                    dp_mapping.insert(
                        dp_id,
                        DpInfo {
                            code: raw_dp.code,
                            dp_type,
                        },
                    );
                }
            }

            let name = raw.name.unwrap_or_else(|| raw.id.clone());
            let topic_name = sanitize_topic_name(&name);

            Ok(DeviceConfig {
                name,
                id: raw.id,
                key: raw.key,
                ip,
                topic_name,
                dp_mapping,
                reverse_mapping,
            })
        })
        .collect()
}

/// Convert a device name into a safe MQTT topic segment.
/// "Solar Heat Pump" → "solar_heat_pump"
fn sanitize_topic_name(name: &str) -> String {
    name.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_lowercase()
            } else {
                '_'
            }
        })
        .collect::<String>()
        .trim_matches('_')
        .to_string()
}

fn parse_dp_type(raw: &RawDpMapping) -> DpType {
    match raw.dp_type.as_str() {
        "Boolean" => DpType::Boolean,
        "Integer" => DpType::Integer,
        "Enum" => {
            let range = raw
                .values
                .as_ref()
                .and_then(|v| v.get("range"))
                .and_then(|r| r.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            DpType::Enum(range)
        }
        "Bitmap" => DpType::Bitmap,
        _ => DpType::Integer, // fallback
    }
}

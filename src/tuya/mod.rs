pub mod client;

/// A DP state update from the Tuya device, ready to publish to MQTT.
pub struct DpUpdate {
    pub topic_name: String,
    pub dp_code: String,
    pub value: String,
}

/// A command to send to the Tuya device (dp_id → JSON value).
pub struct DpCommand {
    pub dps: serde_json::Value,
}

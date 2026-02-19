use std::collections::HashMap;

use rumqttc::{AsyncClient, Event, EventLoop, Incoming, MqttOptions, QoS};
use tokio::sync::mpsc;
use tracing::{error, info, warn};

use crate::config::Config;
use crate::tuya::DpUpdate;

pub struct MqttMessage {
    pub topic: String,
    pub payload: String,
}

pub struct MqttClient {
    client: AsyncClient,
    eventloop: EventLoop,
    config: Config,
}

impl MqttClient {
    pub fn new(config: &Config) -> Self {
        let mut mqttopts = MqttOptions::new(
            &config.mqtt.client_id,
            &config.mqtt.broker_host,
            config.mqtt.broker_port,
        );
        mqttopts.set_keep_alive(std::time::Duration::from_secs(30));

        if let (Some(user), Some(pass)) = (&config.mqtt.username, &config.mqtt.password) {
            mqttopts.set_credentials(user, pass);
        }

        // LWT: publish "offline" on disconnect. Use first device's status topic for
        // single-device setups; generic bridge topic for multi-device.
        let lwt_topic = if config.devices.len() == 1 {
            config.device_status_topic(&config.devices[0].topic_name)
        } else {
            format!("{}/bridge_status", config.mqtt.topic_prefix)
        };
        let lwt = rumqttc::LastWill::new(
            lwt_topic,
            "offline".as_bytes().to_vec(),
            QoS::AtLeastOnce,
            true,
        );
        mqttopts.set_last_will(lwt);

        let (client, eventloop) = AsyncClient::new(mqttopts, 100);

        Self {
            client,
            eventloop,
            config: config.clone(),
        }
    }

    /// Run the MQTT event loop. Subscribes to command topics on connect,
    /// forwards incoming publish messages through command_tx, and publishes
    /// DP state updates received from dp_rx.
    pub async fn run(
        mut self,
        command_tx: mpsc::Sender<MqttMessage>,
        mut dp_rx: mpsc::Receiver<DpUpdate>,
    ) {
        let subscribe_topics: Vec<String> = self
            .config
            .devices
            .iter()
            .map(|d| self.config.device_command_topic(&d.topic_name))
            .collect();

        let mut last_values: HashMap<String, String> = HashMap::new();

        loop {
            tokio::select! {
                event = self.eventloop.poll() => {
                    match event {
                        Ok(event) => {
                            if let Event::Incoming(incoming) = &event {
                                match incoming {
                                    Incoming::ConnAck(_) => {
                                        info!("Connected to MQTT broker");

                                        // Publish per-device bridge_status = online
                                        for device in &self.config.devices {
                                            let topic =
                                                self.config.device_status_topic(&device.topic_name);
                                            if let Err(e) = self
                                                .client
                                                .publish(&topic, QoS::AtLeastOnce, true, "online")
                                                .await
                                            {
                                                error!("Failed to publish online status: {}", e);
                                            }
                                        }

                                        // Subscribe to command topics
                                        for topic in &subscribe_topics {
                                            if let Err(e) = self
                                                .client
                                                .subscribe(topic, QoS::AtLeastOnce)
                                                .await
                                            {
                                                error!("Failed to subscribe to {}: {}", topic, e);
                                            }
                                        }
                                    }
                                    Incoming::Publish(publish) => {
                                        let payload =
                                            String::from_utf8_lossy(&publish.payload).to_string();
                                        let msg = MqttMessage {
                                            topic: publish.topic.clone(),
                                            payload,
                                        };
                                        if command_tx.send(msg).await.is_err() {
                                            warn!("Command channel closed");
                                        }
                                    }
                                    _ => {}
                                }
                            }
                        }
                        Err(e) => {
                            error!("MQTT connection error: {}. Reconnecting...", e);
                            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                        }
                    }
                }
                Some(update) = dp_rx.recv() => {
                    let cache_key = format!("{}/{}", update.topic_name, update.dp_code);
                    if last_values.get(&cache_key) != Some(&update.value) {
                        last_values.insert(cache_key, update.value.clone());
                        let topic = format!(
                            "{}/{}/state/{}",
                            self.config.mqtt.topic_prefix, update.topic_name, update.dp_code
                        );
                        let retain = !matches!(
                            update.dp_code.as_str(),
                            "solar_power" | "grid_power" | "grid_percent"
                        );
                        info!("Publishing {}: {}", topic, update.value);
                        if let Err(e) = self
                            .client
                            .publish(&topic, QoS::AtMostOnce, retain, update.value.as_bytes())
                            .await
                        {
                            warn!("Failed to publish {}: {}", topic, e);
                            continue;
                        }
                        // Drive the event loop to immediately flush this publish to the socket
                        match self.eventloop.poll().await {
                            Ok(Event::Incoming(Incoming::Publish(publish))) => {
                                let payload =
                                    String::from_utf8_lossy(&publish.payload).to_string();
                                let msg = MqttMessage {
                                    topic: publish.topic.clone(),
                                    payload,
                                };
                                let _ = command_tx.send(msg).await;
                            }
                            Err(e) => {
                                error!("MQTT error after publish flush: {}", e);
                            }
                            _ => {}
                        }
                    }
                }
            }
        }
    }
}

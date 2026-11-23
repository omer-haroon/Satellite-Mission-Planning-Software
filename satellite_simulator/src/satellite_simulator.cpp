#include <iostream>
#include <fstream>
#include <string>
#include <thread>
#include <chrono>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <random>
#include <vector>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <atomic>
#include <algorithm>
#include <memory>
#include <mqtt/async_client.h>   // Eclipse Paho MQTT C++

// ============================================================
// SIMPLIFIED CCSDS-INSPIRED TELEMETRY PACKET
// ============================================================
//
// This is NOT a full CCSDS implementation.
//
// Packet:
//
// HEADER - 8 bytes
//   Version       : 1 byte
//   Packet Type   : 1 byte
//   Application ID: 2 bytes
//   Sequence      : 2 bytes
//   Payload Length: 2 bytes
//
// PAYLOAD - 16 bytes
//   Timestamp     : 4 bytes
//   Battery Volt  : 2 bytes
//   Battery Curr  : 2 bytes
//   Temperature   : 2 bytes
//   Sensor Value  : 2 bytes
//   OBC Status    : 1 byte   (1 = NOMINAL,      2 = FAULT)
//   ADCS Mode     : 1 byte   (1 = SUN_POINTING, 2 = FAULT)
//   COMMS Status  : 1 byte   (1 = OK,           2 = LOST)
//   Payload ON    : 1 byte
//
// TOTAL = 24 bytes
//
// ============================================================


// ============================================================
// TELEMETRY DATA STRUCTURE
// ============================================================

struct TelemetryData
{
    uint32_t timestamp;

    double battery_voltage;
    double battery_current;
    double temperature;
    double sensor_value;

    uint8_t obc_status;
    uint8_t adcs_mode;
    uint8_t comms_status;
    uint8_t payload_on;
};


// ============================================================
// PACKET HEADER
// ============================================================

struct PacketHeader
{
    uint8_t version;
    uint8_t packet_type;

    uint16_t application_id;
    uint16_t sequence;

    uint16_t payload_length;
};


// ============================================================
// COMPLETE PACKET
// ============================================================

struct TelemetryPacket
{
    PacketHeader header;
    TelemetryData data;
};


// ============================================================
// GET CURRENT UTC TIMESTAMP AS STRING
// ============================================================

std::string get_timestamp_string()
{
    std::time_t now = std::time(nullptr);

    std::tm* utc_time = std::gmtime(&now);

    std::ostringstream timestamp;

    timestamp << std::put_time(
        utc_time,
        "%Y-%m-%dT%H:%M:%SZ"
    );

    return timestamp.str();
}


// ============================================================
// GET UNIX TIMESTAMP
// ============================================================

uint32_t get_unix_timestamp()
{
    return static_cast<uint32_t>(std::time(nullptr));
}


// ============================================================
// WRITE 16-BIT INTEGER IN BIG-ENDIAN FORMAT
// ============================================================

void write_uint16(std::vector<uint8_t>& packet, uint16_t value)
{
    packet.push_back(
        static_cast<uint8_t>((value >> 8) & 0xFF)
    );

    packet.push_back(
        static_cast<uint8_t>(value & 0xFF)
    );
}


// ============================================================
// WRITE 32-BIT INTEGER IN BIG-ENDIAN FORMAT
// ============================================================

void write_uint32(std::vector<uint8_t>& packet, uint32_t value)
{
    packet.push_back(
        static_cast<uint8_t>((value >> 24) & 0xFF)
    );

    packet.push_back(
        static_cast<uint8_t>((value >> 16) & 0xFF)
    );

    packet.push_back(
        static_cast<uint8_t>((value >> 8) & 0xFF)
    );

    packet.push_back(
        static_cast<uint8_t>(value & 0xFF)
    );
}


// ============================================================
// READ 16-BIT INTEGER
// ============================================================

uint16_t read_uint16(
    const std::vector<uint8_t>& packet,
    size_t& position
)
{
    uint16_t value =
        (static_cast<uint16_t>(packet[position]) << 8)
        |
        static_cast<uint16_t>(packet[position + 1]);

    position += 2;

    return value;
}


// ============================================================
// READ 32-BIT INTEGER
// ============================================================

uint32_t read_uint32(
    const std::vector<uint8_t>& packet,
    size_t& position
)
{
    uint32_t value =
        (static_cast<uint32_t>(packet[position]) << 24)
        |
        (static_cast<uint32_t>(packet[position + 1]) << 16)
        |
        (static_cast<uint32_t>(packet[position + 2]) << 8)
        |
        static_cast<uint32_t>(packet[position + 3]);

    position += 4;

    return value;
}


// ============================================================
// ENCODER
// ============================================================
//
// Converts structured telemetry into binary packet.
//
// Telemetry values are scaled before being stored:
//
// Voltage 8.21 V
//        -> 821
//
// Current 0.37 A
//        -> 37
//
// Temperature 41.35 C
//        -> 4135
//
// Sensor 100.50
//        -> 10050
//
// This allows decimal values to be stored using integers.
// ============================================================

std::vector<uint8_t> encode_packet(
    const TelemetryPacket& telemetry_packet
)
{
    std::vector<uint8_t> packet;

    // ---------------- HEADER ----------------

    packet.push_back(
        telemetry_packet.header.version
    );

    packet.push_back(
        telemetry_packet.header.packet_type
    );

    write_uint16(
        packet,
        telemetry_packet.header.application_id
    );

    write_uint16(
        packet,
        telemetry_packet.header.sequence
    );

    write_uint16(
        packet,
        telemetry_packet.header.payload_length
    );


    // ---------------- PAYLOAD ----------------

    write_uint32(
        packet,
        telemetry_packet.data.timestamp
    );


    // Battery voltage ×100
    uint16_t battery_voltage =
        static_cast<uint16_t>(
            telemetry_packet.data.battery_voltage * 100.0
        );

    write_uint16(
        packet,
        battery_voltage
    );


    // Battery current ×100
    uint16_t battery_current =
        static_cast<uint16_t>(
            telemetry_packet.data.battery_current * 100.0
        );

    write_uint16(
        packet,
        battery_current
    );


    // Temperature ×100
    int16_t temperature =
        static_cast<int16_t>(
            telemetry_packet.data.temperature * 100.0
        );

    write_uint16(
        packet,
        static_cast<uint16_t>(temperature)
    );


    // Sensor value ×100
    uint16_t sensor_value =
        static_cast<uint16_t>(
            telemetry_packet.data.sensor_value * 100.0
        );

    write_uint16(
        packet,
        sensor_value
    );


    // Status fields

    packet.push_back(
        telemetry_packet.data.obc_status
    );

    packet.push_back(
        telemetry_packet.data.adcs_mode
    );

    packet.push_back(
        telemetry_packet.data.comms_status
    );

    packet.push_back(
        telemetry_packet.data.payload_on
    );


    return packet;
}


// ============================================================
// DECODER
// ============================================================
//
// Takes binary packet and reconstructs the telemetry data.
// ============================================================

bool decode_packet(
    const std::vector<uint8_t>& packet,
    TelemetryPacket& telemetry_packet
)
{
    // Minimum packet size:
    // 8 byte header + 16 byte payload = 24 bytes

    if (packet.size() < 24)
    {
        return false;
    }

    size_t position = 0;


    // ---------------- HEADER ----------------

    telemetry_packet.header.version =
        packet[position++];

    telemetry_packet.header.packet_type =
        packet[position++];

    telemetry_packet.header.application_id =
        read_uint16(packet, position);

    telemetry_packet.header.sequence =
        read_uint16(packet, position);

    telemetry_packet.header.payload_length =
        read_uint16(packet, position);


    // Check payload length

    if (telemetry_packet.header.payload_length != 16)
    {
        return false;
    }


    // Check actual packet size

    if (packet.size() !=
        8 + telemetry_packet.header.payload_length)
    {
        return false;
    }


    // ---------------- PAYLOAD ----------------

    telemetry_packet.data.timestamp =
        read_uint32(packet, position);


    uint16_t voltage =
        read_uint16(packet, position);

    telemetry_packet.data.battery_voltage =
        voltage / 100.0;


    uint16_t current =
        read_uint16(packet, position);

    telemetry_packet.data.battery_current =
        current / 100.0;


    uint16_t temperature_raw =
        read_uint16(packet, position);

    int16_t temperature =
        static_cast<int16_t>(temperature_raw);

    telemetry_packet.data.temperature =
        temperature / 100.0;


    uint16_t sensor =
        read_uint16(packet, position);

    telemetry_packet.data.sensor_value =
        sensor / 100.0;


    telemetry_packet.data.obc_status =
        packet[position++];

    telemetry_packet.data.adcs_mode =
        packet[position++];

    telemetry_packet.data.comms_status =
        packet[position++];

    telemetry_packet.data.payload_on =
        packet[position++];


    return true;
}


// ============================================================
// CONVERT PACKET TO HEX STRING
// ============================================================

std::string packet_to_hex(
    const std::vector<uint8_t>& packet
)
{
    std::ostringstream output;

    output << std::hex
           << std::uppercase
           << std::setfill('0');

    for (uint8_t byte : packet)
    {
        output
            << std::setw(2)
            << static_cast<int>(byte)
            << " ";
    }

    return output.str();
}



// ============================================================
// MQTT / FLEET CONFIGURATION
// ============================================================
//
// All settings come from environment variables so the same
// binary works on a laptop, in Docker, or on the project server
// without recompiling.
//
//   SAT_COUNT      number of satellites to simulate, named
//                  SAT-01, SAT-02, ...        (default: 1, max 500)
//   SAT_IDS        explicit comma-separated ids, e.g.
//                  "SAT-01,PK-SAT-2,CUBE_7"   (overrides SAT_COUNT)
//   MQTT_HOST      broker host                (default: localhost)
//   MQTT_PORT      broker port                (default: 1883, 8883 with TLS)
//   MQTT_TLS       "1" to use ssl://          (default: off)
//   MQTT_CA_FILE   CA certificate (PEM)       (only used with TLS)
//   MQTT_USER      username                   (default: none)
//   MQTT_PASS      password                   (default: none)
//   MQTT_MODE      combined | subsystem | both    (default: both)
//   SIM_VERBOSE    "1" = print hex dump + decoded values for every packet
//
// ============================================================

enum class PublishMode
{
    COMBINED,
    SUBSYSTEM,
    BOTH
};

struct MqttConfig
{
    std::string host = "localhost";
    std::string port = "1883";
    bool use_tls = false;
    std::string ca_file;
    std::string username;
    std::string password;
    std::vector<std::string> sat_ids;
    PublishMode mode = PublishMode::BOTH;
    int qos = 1;   // at-least-once: telemetry must not be silently lost
    bool verbose = false;

    std::string server_uri() const
    {
        return std::string(use_tls ? "ssl://" : "tcp://")
               + host + ":" + port;
    }

    static std::string base_topic(const std::string& sat_id)
    {
        return "mps/" + sat_id;
    }

    static std::string status_topic(const std::string& sat_id)
    {
        return base_topic(sat_id) + "/status";
    }

    static std::string telemetry_topic(
        const std::string& sat_id, const std::string& name)
    {
        return base_topic(sat_id) + "/telemetry/" + name;
    }
};

std::string env_or(const char* name, const std::string& fallback)
{
    const char* value = std::getenv(name);

    return (value != nullptr && value[0] != '\0')
               ? std::string(value)
               : fallback;
}

std::string trim(const std::string& s)
{
    size_t a = s.find_first_not_of(" \t\r\n");
    size_t b = s.find_last_not_of(" \t\r\n");

    return (a == std::string::npos) ? "" : s.substr(a, b - a + 1);
}

// The id becomes part of an MQTT topic, so characters that have a
// meaning in topics ( / + # ) or whitespace must never get in.
bool valid_sat_id(const std::string& id)
{
    if (id.empty() || id.size() > 32)
    {
        return false;
    }

    for (char c : id)
    {
        bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
               || (c >= '0' && c <= '9') || c == '-' || c == '_';

        if (!ok)
        {
            return false;
        }
    }

    return true;
}

bool build_sat_list(std::vector<std::string>& out, std::string& error)
{
    std::string list = env_or("SAT_IDS", "");

    if (!list.empty())
    {
        std::stringstream ss(list);
        std::string item;

        while (std::getline(ss, item, ','))
        {
            item = trim(item);

            if (item.empty())
            {
                continue;
            }

            if (!valid_sat_id(item))
            {
                error = "invalid satellite id '" + item
                      + "' (allowed: A-Z a-z 0-9 - _ ; max 32 chars)";
                return false;
            }

            if (std::find(out.begin(), out.end(), item) != out.end())
            {
                error = "duplicate satellite id '" + item + "'";
                return false;
            }

            out.push_back(item);
        }

        if (out.empty())
        {
            error = "SAT_IDS contains no satellite ids";
            return false;
        }

        return true;
    }

    int count = 1;

    try
    {
        count = std::stoi(env_or("SAT_COUNT", "1"));
    }
    catch (...)
    {
        error = "SAT_COUNT must be a number";
        return false;
    }

    if (count < 1 || count > 500)
    {
        error = "SAT_COUNT must be between 1 and 500";
        return false;
    }

    for (int i = 1; i <= count; ++i)
    {
        char buf[16];
        std::snprintf(buf, sizeof(buf), "SAT-%02d", i);
        out.push_back(buf);
    }

    return true;
}

bool load_mqtt_config(MqttConfig& cfg, std::string& error)
{
    cfg.use_tls  = (env_or("MQTT_TLS", "0") == "1");
    cfg.host     = env_or("MQTT_HOST", "localhost");
    cfg.port     = env_or("MQTT_PORT", cfg.use_tls ? "8883" : "1883");
    cfg.ca_file  = env_or("MQTT_CA_FILE", "");
    cfg.username = env_or("MQTT_USER", "");
    cfg.password = env_or("MQTT_PASS", "");
    cfg.verbose  = (env_or("SIM_VERBOSE", "0") == "1");

    std::string mode = env_or("MQTT_MODE", "both");

    if (mode == "combined")
    {
        cfg.mode = PublishMode::COMBINED;
    }
    else if (mode == "subsystem")
    {
        cfg.mode = PublishMode::SUBSYSTEM;
    }
    else
    {
        cfg.mode = PublishMode::BOTH;
    }

    return build_sat_list(cfg.sat_ids, error);
}


// ============================================================
// FAULT INJECTION
// ============================================================
//
// Every satellite occasionally develops ONE of five faults. A
// fault starts at a random moment, lasts a random number of
// seconds and then clears. While it is active it shows up in the
// telemetry values themselves (nothing extra is added to the
// JSON, so the topic contract is unchanged):
//
//   LOW_BATTERY  battery_v sinks gradually to ~6.6 V
//                (below 7.0 V counts as low), recovers afterwards
//   HIGH_TEMP    temp_c climbs gradually to ~75 C
//                (above 60 C counts as high), cools afterwards
//   COMMS_LOSS   comms.link_status      OK           -> LOST
//   OBC_FAULT    obc.status             NOMINAL      -> FAULT
//   ADCS_FAULT   adcs.mode              SUN_POINTING -> FAULT
//
// Settings (environment variables):
//
//   FAULT_INJECTION        "0" switches faults off     (default: 1)
//   FAULT_MEAN_INTERVAL_S  average quiet time between faults of
//                          ONE satellite, in seconds   (default: 180)
//   FAULT_TYPES            comma separated subset of
//                          low_battery,high_temp,comms_loss,
//                          obc_fault,adcs_fault        (default: all)
//
// Satellites are independent: each one draws its own faults.
// ============================================================

constexpr uint8_t CODE_OK    = 1;   // NOMINAL / SUN_POINTING / OK
constexpr uint8_t CODE_FAULT = 2;   // FAULT   / FAULT        / LOST

constexpr double LOW_BATTERY_V = 7.0;    // below this = "low battery"
constexpr double HIGH_TEMP_C   = 60.0;   // above this = "high temperature"

constexpr double LOW_BATTERY_FLOOR_V = 6.6;    // where a battery fault bottoms out
constexpr double HIGH_TEMP_PEAK_C    = 75.0;   // where a thermal fault levels off
constexpr double BATTERY_STEP_V      = 0.04;   // V per second, falling and recovering
constexpr double HEATING_STEP_C      = 1.5;    // C per second while heating
constexpr double COOLING_STEP_C      = 1.0;    // C per second while cooling down

enum class FaultType
{
    NONE,
    LOW_BATTERY,
    HIGH_TEMPERATURE,
    COMM_LOSS,
    OBC_FAULT,
    ADCS_FAULT
};

struct FaultProfile
{
    FaultType type;
    const char* env_name;       // name used in FAULT_TYPES
    const char* display_name;   // name used in console messages
    int min_seconds;            // how long the fault lasts (random in range)
    int max_seconds;
};

// Battery/thermal faults last longer so the slow ramp has time to
// cross the low-battery / high-temperature limits.
const FaultProfile FAULT_PROFILES[] = {
    { FaultType::LOW_BATTERY,      "low_battery", "LOW_BATTERY", 45, 90 },
    { FaultType::HIGH_TEMPERATURE, "high_temp",   "HIGH_TEMP",   30, 75 },
    { FaultType::COMM_LOSS,        "comms_loss",  "COMMS_LOSS",  10, 30 },
    { FaultType::OBC_FAULT,        "obc_fault",   "OBC_FAULT",   10, 40 },
    { FaultType::ADCS_FAULT,       "adcs_fault",  "ADCS_FAULT",  15, 45 },
};

const FaultProfile& profile_of(FaultType type)
{
    for (const FaultProfile& p : FAULT_PROFILES)
    {
        if (p.type == type)
        {
            return p;
        }
    }

    return FAULT_PROFILES[0];
}

struct FaultConfig
{
    bool enabled = true;
    double mean_interval_s = 180.0;
    std::vector<FaultType> types;     // faults that may be injected
};

bool load_fault_config(FaultConfig& cfg, std::string& error)
{
    cfg.enabled = (env_or("FAULT_INJECTION", "1") != "0");

    try
    {
        cfg.mean_interval_s = std::stod(env_or("FAULT_MEAN_INTERVAL_S", "180"));
    }
    catch (...)
    {
        error = "FAULT_MEAN_INTERVAL_S must be a number";
        return false;
    }

    if (cfg.mean_interval_s < 1.0)
    {
        error = "FAULT_MEAN_INTERVAL_S must be at least 1 second";
        return false;
    }

    std::string list = env_or("FAULT_TYPES", "");

    if (list.empty())
    {
        for (const FaultProfile& p : FAULT_PROFILES)
        {
            cfg.types.push_back(p.type);
        }

        return true;
    }

    std::stringstream ss(list);
    std::string item;

    while (std::getline(ss, item, ','))
    {
        item = trim(item);

        if (item.empty())
        {
            continue;
        }

        bool found = false;

        for (const FaultProfile& p : FAULT_PROFILES)
        {
            if (item == p.env_name)
            {
                if (std::find(cfg.types.begin(), cfg.types.end(), p.type)
                    == cfg.types.end())
                {
                    cfg.types.push_back(p.type);
                }

                found = true;
                break;
            }
        }

        if (!found)
        {
            error = "unknown fault type '" + item + "' in FAULT_TYPES "
                    "(allowed: low_battery,high_temp,comms_loss,"
                    "obc_fault,adcs_fault)";
            return false;
        }
    }

    if (cfg.types.empty())
    {
        error = "FAULT_TYPES contains no fault types";
        return false;
    }

    return true;
}

// Move 'value' towards 'target' by at most 'max_step'.
double approach(double value, double target, double max_step)
{
    if (target > value)
    {
        return std::min(value + max_step, target);
    }

    return std::max(value - max_step, target);
}


// ============================================================
// JSON HELPERS
// ============================================================
//
// The telemetry values come from the DECODED packet (i.e. what
// the receiving side would actually see), not from the raw
// simulator variables.
// ============================================================

std::string unix_to_iso(uint32_t unix_time)
{
    std::time_t t = static_cast<std::time_t>(unix_time);
    std::tm utc{};

#ifdef _WIN32
    gmtime_s(&utc, &t);
#else
    gmtime_r(&t, &utc);
#endif

    std::ostringstream out;
    out << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
    return out.str();
}

std::string num2(double value)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(2) << value;
    return out.str();
}

std::string obc_status_name(uint8_t code)
{
    return code == CODE_OK    ? "NOMINAL"
         : code == CODE_FAULT ? "FAULT"
                              : "UNKNOWN";
}

std::string adcs_mode_name(uint8_t code)
{
    return code == CODE_OK    ? "SUN_POINTING"
         : code == CODE_FAULT ? "FAULT"
                              : "UNKNOWN";
}

std::string comms_status_name(uint8_t code)
{
    return code == CODE_OK    ? "OK"
         : code == CODE_FAULT ? "LOST"
                              : "UNKNOWN";
}

// Which fault conditions are visible in a (decoded) packet? Derived
// from the values themselves - exactly what the ground station sees.
std::string fault_flags(const TelemetryData& d)
{
    std::string flags;

    auto add = [&](const char* name)
    {
        if (!flags.empty())
        {
            flags += ",";
        }
        flags += name;
    };

    if (d.battery_voltage < LOW_BATTERY_V) add("LOW_BATTERY");
    if (d.temperature > HIGH_TEMP_C)       add("HIGH_TEMP");
    if (d.comms_status == CODE_FAULT)      add("COMMS_LOST");
    if (d.obc_status == CODE_FAULT)        add("OBC_FAULT");
    if (d.adcs_mode == CODE_FAULT)         add("ADCS_FAULT");

    return flags;
}

// ---- Per-subsystem field fragments (no braces) ----

std::string eps_fields(const TelemetryData& d)
{
    return "\"battery_v\":" + num2(d.battery_voltage)
         + ",\"battery_i\":" + num2(d.battery_current)
         + ",\"temp_c\":" + num2(d.temperature);
}

std::string obc_fields(const TelemetryData& d)
{
    return "\"status\":\"" + obc_status_name(d.obc_status) + "\"";
}

std::string adcs_fields(const TelemetryData& d)
{
    return "\"mode\":\"" + adcs_mode_name(d.adcs_mode) + "\"";
}

std::string comms_fields(const TelemetryData& d)
{
    return "\"link_status\":\"" + comms_status_name(d.comms_status) + "\"";
}

std::string payload_fields(const TelemetryData& d)
{
    return std::string("\"payload_on\":")
         + (d.payload_on ? "true" : "false")
         + ",\"sensor_value\":" + num2(d.sensor_value);
}

// ---- One COMBINED message: the whole packet, nested by subsystem ----
// Same layout as the existing logs/telemetry_log.jsonl lines.

std::string build_combined_json(
    const std::string& sat_id,
    const TelemetryPacket& p,
    size_t size_bytes)
{
    std::ostringstream j;

    j << "{\"sat_id\":\"" << sat_id << "\""
      << ",\"timestamp\":\"" << unix_to_iso(p.data.timestamp) << "\""
      << ",\"packet\":{"
      << "\"version\":" << static_cast<int>(p.header.version)
      << ",\"type\":" << static_cast<int>(p.header.packet_type)
      << ",\"application_id\":" << p.header.application_id
      << ",\"sequence\":" << p.header.sequence
      << ",\"payload_length\":" << p.header.payload_length
      << ",\"size_bytes\":" << size_bytes << "}"
      << ",\"eps\":{" << eps_fields(p.data) << "}"
      << ",\"obc\":{" << obc_fields(p.data) << "}"
      << ",\"adcs\":{" << adcs_fields(p.data) << "}"
      << ",\"comms\":{" << comms_fields(p.data) << "}"
      << ",\"payload\":{" << payload_fields(p.data) << "}"
      << "}";

    return j.str();
}

// ---- One SUBSYSTEM message: flat, self-contained ----
// Every subsystem message repeats sat_id / timestamp / sequence so
// a consumer can handle each topic on its own.

std::string build_subsystem_json(
    const std::string& sat_id,
    const TelemetryPacket& p,
    const std::string& fields)
{
    std::ostringstream j;

    j << "{\"sat_id\":\"" << sat_id << "\""
      << ",\"timestamp\":\"" << unix_to_iso(p.data.timestamp) << "\""
      << ",\"sequence\":" << p.header.sequence
      << "," << fields
      << "}";

    return j.str();
}


// ============================================================
// MQTT PUBLISHER  (one connection per satellite)
// ============================================================
//
// Each satellite is its own MQTT client, like real spacecraft
// would be separate ground-link sources. Benefits:
//   * its own Last Will  -> the broker reports THAT satellite
//     offline if it drops, not the whole fleet
//   * the broker can later authorise each satellite to publish
//     only under its own topic (mps/<its id>/#)
//
// Nothing here blocks: connecting happens in the background, so
// an unreachable broker can never stall the 1-second telemetry
// cadence of the other satellites.
// ============================================================

class MqttPublisher
{
public:
    MqttPublisher(const MqttConfig& cfg, const std::string& sat_id)
        : cfg_(cfg),
          sat_id_(sat_id),
          client_(cfg.server_uri(), "mps-simulator-" + sat_id)
    {
        mqtt::connect_options_builder builder;

        builder.clean_session(true)
               .keep_alive_interval(std::chrono::seconds(20))
               .connect_timeout(std::chrono::seconds(3));

        if (!cfg_.username.empty())
        {
            builder.user_name(cfg_.username).password(cfg_.password);
        }

        if (cfg_.use_tls)
        {
            mqtt::ssl_options_builder ssl;
            ssl.enable_server_cert_auth(true);

            if (!cfg_.ca_file.empty())
            {
                ssl.trust_store(cfg_.ca_file);
            }

            builder.ssl(ssl.finalize());
        }

        // Last Will: if this satellite dies without saying goodbye,
        // the BROKER publishes this on its behalf.
        builder.will(mqtt::message(
            MqttConfig::status_topic(sat_id_),
            status_json("offline"),
            1,
            true));

        options_ = builder.finalize();
    }

    std::string status_json(const std::string& state) const
    {
        return "{\"sat_id\":\"" + sat_id_ + "\",\"state\":\""
               + state + "\"}";
    }

    bool connected() const
    {
        return client_.is_connected();
    }

    // Call every tick. Starts a background (re)connect when needed,
    // announces "online" once connected, never blocks.
    bool ensure_connected()
    {
        if (client_.is_connected())
        {
            if (!announced_)
            {
                announced_ = true;

                publish_async(
                    MqttConfig::status_topic(sat_id_),
                    status_json("online"),
                    true);
            }

            return true;
        }

        announced_ = false;

        if (pending_ && !pending_->is_complete())
        {
            return false;   // an attempt is already in flight
        }

        pending_.reset();

        auto now = std::chrono::steady_clock::now();

        if (attempted_ && now - last_attempt_ < std::chrono::seconds(5))
        {
            return false;
        }

        attempted_ = true;
        last_attempt_ = now;

        try
        {
            pending_ = client_.connect(options_);
        }
        catch (const mqtt::exception&)
        {
            pending_.reset();
        }

        return false;
    }

    // Queue one message; returns a token to wait on (nullptr = not sent).
    mqtt::delivery_token_ptr publish_async(
        const std::string& topic,
        const std::string& payload,
        bool retained = false)
    {
        if (!client_.is_connected())
        {
            return nullptr;
        }

        try
        {
            return client_.publish(topic, payload, cfg_.qos, retained);
        }
        catch (const mqtt::exception& e)
        {
            std::cerr << "[MQTT] publish to " << topic
                      << " failed: " << e.what() << std::endl;
            return nullptr;
        }
    }

    void shutdown()
    {
        if (!client_.is_connected())
        {
            return;
        }

        try
        {
            auto t = publish_async(
                MqttConfig::status_topic(sat_id_),
                status_json("offline"),
                true);

            if (t)
            {
                t->wait_for(std::chrono::seconds(1));
            }

            client_.disconnect()->wait_for(std::chrono::seconds(2));
        }
        catch (const mqtt::exception&)
        {
        }
    }

private:
    MqttConfig cfg_;
    std::string sat_id_;
    mqtt::async_client client_;
    mqtt::connect_options options_;
    mqtt::token_ptr pending_;
    bool attempted_ = false;
    bool announced_ = false;
    std::chrono::steady_clock::time_point last_attempt_;
};

typedef std::vector<mqtt::delivery_token_ptr> TokenList;

// Queue all messages for one decoded packet according to MQTT_MODE.
TokenList publish_packet(
    MqttPublisher& mqtt,
    const MqttConfig& cfg,
    const std::string& sat_id,
    const TelemetryPacket& p,
    size_t size_bytes)
{
    TokenList tokens;

    if (cfg.mode == PublishMode::COMBINED || cfg.mode == PublishMode::BOTH)
    {
        tokens.push_back(mqtt.publish_async(
            MqttConfig::telemetry_topic(sat_id, "all"),
            build_combined_json(sat_id, p, size_bytes)));
    }

    if (cfg.mode == PublishMode::SUBSYSTEM || cfg.mode == PublishMode::BOTH)
    {
        struct Item { const char* name; std::string fields; };

        const Item items[] = {
            { "eps",     eps_fields(p.data)     },
            { "obc",     obc_fields(p.data)     },
            { "adcs",    adcs_fields(p.data)    },
            { "comms",   comms_fields(p.data)   },
            { "payload", payload_fields(p.data) },
        };

        for (const Item& item : items)
        {
            tokens.push_back(mqtt.publish_async(
                MqttConfig::telemetry_topic(sat_id, item.name),
                build_subsystem_json(sat_id, p, item.fields)));
        }
    }

    return tokens;
}

// Wait (briefly) for the broker's acknowledgements; returns how many arrived.
int count_acked(const TokenList& tokens)
{
    int ok = 0;

    for (const auto& token : tokens)
    {
        try
        {
            if (token && token->wait_for(std::chrono::milliseconds(500)))
            {
                ++ok;
            }
        }
        catch (const mqtt::exception&)
        {
        }
    }

    return ok;
}


// ============================================================
// ONE SIMULATED SATELLITE
// ============================================================
//
// Owns everything that makes a satellite a separate entity:
// its id, CCSDS application id, packet sequence counter,
// physical state, random generator and MQTT connection.
// ============================================================

class Satellite
{
public:
    Satellite(const std::string& id,
              uint16_t application_id,
              const MqttConfig& cfg,
              const FaultConfig& faults,
              uint32_t seed)
        : id_(id),
          application_id_(application_id),
          generator_(seed),
          mqtt_(cfg, id),
          faults_(faults)
    {
        // Give every satellite a slightly different starting condition
        // so the fleet does not produce identical data.
        std::uniform_real_distribution<double> dv(-0.15, 0.15);
        std::uniform_real_distribution<double> dt(-3.0, 3.0);
        std::uniform_real_distribution<double> ds(-5.0, 5.0);

        battery_voltage_ = 8.20 + dv(generator_);
        temperature_     = 41.00 + dt(generator_);
        sensor_value_    = 100.00 + ds(generator_);
    }

    const std::string& id() const { return id_; }
    MqttPublisher& mqtt() { return mqtt_; }

    // Advance the simulation one step and return the next raw packet.
    TelemetryPacket next_packet()
    {
        std::uniform_real_distribution<double> voltage_change(-0.01, 0.01);
        std::uniform_real_distribution<double> current_change(-0.05, 0.05);
        std::uniform_real_distribution<double> temperature_change(-0.50, 0.50);
        std::uniform_real_distribution<double> sensor_change(-0.50, 0.50);

        battery_voltage_ += voltage_change(generator_);
        battery_current_  = 0.40 + current_change(generator_);
        temperature_     += temperature_change(generator_);
        sensor_value_    += sensor_change(generator_);

        // Possibly start / end a fault, and move the fault offsets
        // that are added on top of the normal readings below.
        update_faults();

        TelemetryPacket p;

        p.header.version = 1;
        p.header.packet_type = 1;
        p.header.application_id = application_id_;
        p.header.sequence = sequence_++;      // per-satellite counter
        p.header.payload_length = 16;

        p.data.timestamp = get_unix_timestamp();
        // Normal reading + fault offset (offset is 0 when healthy).
        // Voltage is clamped at 0 because the packet field is unsigned.
        p.data.battery_voltage = std::max(0.0, battery_voltage_ + battery_offset_);
        p.data.battery_current = battery_current_;
        p.data.temperature = temperature_ + temperature_offset_;
        p.data.sensor_value = sensor_value_;

        p.data.obc_status =
            (active_fault_ == FaultType::OBC_FAULT) ? CODE_FAULT : CODE_OK;
        p.data.adcs_mode =
            (active_fault_ == FaultType::ADCS_FAULT) ? CODE_FAULT : CODE_OK;
        p.data.comms_status =
            (active_fault_ == FaultType::COMM_LOSS) ? CODE_FAULT : CODE_OK;
        p.data.payload_on = 1;     // 1 = ON

        return p;
    }

private:
    // Fault state machine, called once per tick (= once per second).
    //   * no fault active -> small chance per tick that one starts
    //     (1 / mean interval, so one fault per mean interval on average)
    //   * fault active    -> count down, clear when the time is up
    // Battery and temperature never jump: their offsets are eased
    // towards the fault value (and back to 0 afterwards), so the
    // dashboard sees a realistic ramp instead of a step.
    void update_faults()
    {
        if (active_fault_ != FaultType::NONE)
        {
            if (--fault_ticks_left_ <= 0)
            {
                std::cout << "[FAULT] " << id_ << "  "
                          << profile_of(active_fault_).display_name
                          << " cleared" << std::endl;

                active_fault_ = FaultType::NONE;
            }
        }
        else if (faults_.enabled && !faults_.types.empty())
        {
            std::bernoulli_distribution begins(1.0 / faults_.mean_interval_s);

            if (begins(generator_))
            {
                std::uniform_int_distribution<size_t> pick(
                    0, faults_.types.size() - 1);

                active_fault_ = faults_.types[pick(generator_)];

                const FaultProfile& profile = profile_of(active_fault_);

                std::uniform_int_distribution<int> length(
                    profile.min_seconds, profile.max_seconds);

                fault_ticks_left_ = length(generator_);

                std::cout << "[FAULT] " << id_ << "  "
                          << profile.display_name << " injected (for about "
                          << fault_ticks_left_ << " s)" << std::endl;
            }
        }

        std::uniform_real_distribution<double> jitter_v(-0.03, 0.03);
        std::uniform_real_distribution<double> jitter_t(-1.00, 1.00);

        // Battery: sink to the floor during LOW_BATTERY, else recover.
        double battery_target = 0.0;

        if (active_fault_ == FaultType::LOW_BATTERY)
        {
            battery_target = std::min(
                0.0,
                LOW_BATTERY_FLOOR_V + jitter_v(generator_) - battery_voltage_);
        }

        battery_offset_ = approach(
            battery_offset_, battery_target, BATTERY_STEP_V);

        // Temperature: heat up during HIGH_TEMP, else cool down.
        double temperature_target = 0.0;

        if (active_fault_ == FaultType::HIGH_TEMPERATURE)
        {
            temperature_target = std::max(
                0.0,
                HIGH_TEMP_PEAK_C + jitter_t(generator_) - temperature_);
        }

        temperature_offset_ = approach(
            temperature_offset_,
            temperature_target,
            temperature_target > temperature_offset_ ? HEATING_STEP_C
                                                     : COOLING_STEP_C);
    }

    std::string id_;
    uint16_t application_id_;
    std::mt19937 generator_;
    MqttPublisher mqtt_;
    FaultConfig faults_;

    double battery_voltage_ = 8.20;
    double battery_current_ = 0.40;
    double temperature_ = 41.00;
    double sensor_value_ = 100.00;
    uint16_t sequence_ = 0;

    // fault state
    FaultType active_fault_ = FaultType::NONE;
    int fault_ticks_left_ = 0;
    double battery_offset_ = 0.0;       // volts added to the battery reading
    double temperature_offset_ = 0.0;   // degrees C added to the temperature
};


// ============================================================
// CLEAN SHUTDOWN ON CTRL+C
// ============================================================

std::atomic<bool> g_running{true};

void handle_signal(int)
{
    g_running = false;
}


// ============================================================
// MAIN
// ============================================================

int main()
{
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    MqttConfig cfg;
    std::string config_error;

    if (!load_mqtt_config(cfg, config_error))
    {
        std::cerr << "CONFIG ERROR: " << config_error << std::endl;
        return 1;
    }

    FaultConfig fault_cfg;

    if (!load_fault_config(fault_cfg, config_error))
    {
        std::cerr << "CONFIG ERROR: " << config_error << std::endl;
        return 1;
    }

    // One shared black-box recorder; every line carries its sat_id.
    std::ofstream log_file(
        "logs/telemetry_log.jsonl",
        std::ios::app
    );

    if (!log_file.is_open())
    {
        std::cerr
            << "ERROR: Could not open telemetry log file."
            << std::endl;

        return 1;
    }

    // ---------------- CREATE THE FLEET ----------------

    std::random_device rd;
    std::vector<std::unique_ptr<Satellite>> fleet;

    for (size_t i = 0; i < cfg.sat_ids.size(); ++i)
    {
        // CCSDS application id: 100 for the first satellite, 101 for
        // the second, ... so packets are attributable even without
        // looking at the topic.
        fleet.push_back(std::make_unique<Satellite>(
            cfg.sat_ids[i],
            static_cast<uint16_t>(100 + i),
            cfg,
            fault_cfg,
            rd()));
    }

    std::cout << "Satellite Telemetry Simulator Started..." << std::endl;
    std::cout << "Satellites: " << fleet.size() << "  (";

    for (size_t i = 0; i < fleet.size(); ++i)
    {
        if (i < 4)
        {
            std::cout << (i ? ", " : "") << fleet[i]->id();
        }
        else
        {
            std::cout << ", ... " << fleet.back()->id();
            break;
        }
    }

    std::cout << ")" << std::endl;
    std::cout << "Generating telemetry every 1 second per satellite..." << std::endl;
    std::cout << "CCSDS-inspired packet encoder/decoder ENABLED" << std::endl;

    if (fault_cfg.enabled)
    {
        std::cout << "Fault injection ON: one fault per satellite about every "
                  << fault_cfg.mean_interval_s << " s  (";

        for (size_t i = 0; i < fault_cfg.types.size(); ++i)
        {
            std::cout << (i ? "," : "")
                      << profile_of(fault_cfg.types[i]).env_name;
        }

        std::cout << ")" << std::endl;
    }
    else
    {
        std::cout << "Fault injection OFF" << std::endl;
    }

    std::cout << "MQTT broker: " << cfg.server_uri() << std::endl;
    std::cout << "MQTT topics: mps/<sat_id>/telemetry/"
              << (cfg.mode == PublishMode::COMBINED  ? "all"
                : cfg.mode == PublishMode::SUBSYSTEM ? "{eps,obc,adcs,comms,payload}"
                                                     : "{all,eps,obc,adcs,comms,payload}")
              << std::endl;
    std::cout << "Press CTRL+C to stop." << std::endl;
    std::cout << std::endl;

    // Start every connection in the background, then give the broker
    // up to 3 s to answer so the first packets are not wasted.
    for (auto& sat : fleet)
    {
        sat->mqtt().ensure_connected();
    }

    {
        auto deadline = std::chrono::steady_clock::now()
                      + std::chrono::seconds(3);

        auto all_up = [&]()
        {
            for (auto& sat : fleet)
            {
                if (!sat->mqtt().connected())
                {
                    return false;
                }
            }
            return true;
        };

        while (!all_up()
               && std::chrono::steady_clock::now() < deadline
               && g_running)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    }

    const int expected_per_packet =
        (cfg.mode == PublishMode::BOTH) ? 6
      : (cfg.mode == PublishMode::SUBSYSTEM) ? 5 : 1;

    size_t last_connected_count = static_cast<size_t>(-1);
    auto next_tick = std::chrono::steady_clock::now();

    // ========================================================
    // MAIN SIMULATION LOOP  (one tick = one packet per satellite)
    // ========================================================

    while (g_running)
    {
        struct Result
        {
            std::string line;
            TokenList tokens;
            bool online = false;
        };

        std::vector<Result> results;
        size_t connected_count = 0;

        for (auto& sat : fleet)
        {
            // ---------------- GENERATE + ENCODE ----------------

            TelemetryPacket original_packet = sat->next_packet();

            std::vector<uint8_t> encoded_packet =
                encode_packet(original_packet);

            // ---------------- DECODE ----------------

            TelemetryPacket decoded_packet;

            bool decode_success =
                decode_packet(encoded_packet, decoded_packet);

            if (!decode_success)
            {
                std::cout << sat->id()
                          << "  DECODER: FAILED (packet not published)"
                          << std::endl;
                continue;
            }

            if (cfg.verbose)
            {
                std::cout << "--------------------------------------------" << std::endl;
                std::cout << sat->id() << "  ENCODED PACKET ("
                          << encoded_packet.size() << " bytes)" << std::endl;
                std::cout << "Hex: " << packet_to_hex(encoded_packet) << std::endl;
            }

            // ---------------- JSON LOG ----------------
            // Black-box recorder: keeps every packet even if the
            // broker is down.

            log_file
                << build_combined_json(
                       sat->id(), decoded_packet, encoded_packet.size())
                << '\n';

            // ---------------- PUBLISH OVER MQTT ----------------
            // Queue all messages first; acknowledgements are collected
            // after the whole fleet has been sent.

            Result r;

            std::ostringstream line;
            line << std::fixed << std::setprecision(2)
                 << sat->id()
                 << "  app=" << decoded_packet.header.application_id
                 << " seq=" << decoded_packet.header.sequence
                 << "  V=" << decoded_packet.data.battery_voltage
                 << " I=" << decoded_packet.data.battery_current
                 << " T=" << decoded_packet.data.temperature
                 << " S=" << decoded_packet.data.sensor_value;

            std::string flags = fault_flags(decoded_packet.data);

            if (!flags.empty())
            {
                line << "  ** FAULT: " << flags << " **";
            }

            r.line = line.str();
            r.online = sat->mqtt().ensure_connected();

            if (r.online)
            {
                ++connected_count;

                r.tokens = publish_packet(
                    sat->mqtt(), cfg, sat->id(),
                    decoded_packet, encoded_packet.size());
            }

            results.push_back(std::move(r));
        }

        log_file.flush();

        // ---------------- COLLECT ACKS + REPORT ----------------

        for (auto& r : results)
        {
            if (r.online)
            {
                std::cout << r.line << "  MQTT "
                          << count_acked(r.tokens) << "/"
                          << expected_per_packet << std::endl;
            }
            else
            {
                std::cout << r.line
                          << "  MQTT offline (logged locally only)"
                          << std::endl;
            }
        }

        if (connected_count != last_connected_count)
        {
            last_connected_count = connected_count;

            std::cout << "[MQTT] " << connected_count << "/"
                      << fleet.size()
                      << " satellites connected to broker" << std::endl;
        }

        // ---------------- WAIT UNTIL NEXT 1 s TICK ----------------
        // Fixed cadence (not "sleep 1 s after work"), so the rate
        // stays at 1 Hz however many satellites there are.

        next_tick += std::chrono::seconds(1);

        auto now = std::chrono::steady_clock::now();

        if (now > next_tick + std::chrono::seconds(1))
        {
            next_tick = now;   // fell far behind: resync, don't burst
        }

        while (g_running && std::chrono::steady_clock::now() < next_tick)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
    }

    std::cout << std::endl << "Shutting down..." << std::endl;

    for (auto& sat : fleet)
    {
        sat->mqtt().shutdown();
    }

    log_file.close();

    return 0;
}

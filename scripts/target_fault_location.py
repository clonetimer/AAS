"""Expert-curated fault-source subsystem/component mapping for the uploaded targets.

A target's component describes the injected fault source, not which telemetry
channels are diagnostically affected. Do not conflate this with channel identity.
"""
from __future__ import annotations
import sqlite3

FAULT_LOCATIONS = {
 'pdu.payload': ('eps','pdu_payload_branch','direct_path'),
 'data.storage': ('data','data_storage','direct_path'),
 'star_tracker': ('adcs','star_tracker','direct_path'),
 'propulsion.thruster': ('propulsion','thruster','direct_path'),
 'thermal.electronics': ('thermal','electronics_thermal_control','thermal_domain'),
 'thermal.battery': ('thermal','battery_thermal_control','thermal_domain'),
 'imu.gyro': ('adcs','gyroscope','imu_sensor'),
 'payload.instrument': ('payload','payload_instrument','direct_path'),
 'magnetometer': ('adcs','magnetometer','direct_path'),
 'bus_load': ('eps','electric_bus_load_aggregate','aggregate'),
 'battery.power_path': ('eps','battery_power_path','direct_path'),
 'bus_regulator': ('eps','power_bus_regulator','direct_path'),

 'imu.accelerometer': ('adcs','accelerometer','direct_sensor'),
 'reaction_wheel': ('adcs','reaction_wheel','direct_actuator'),
 'reaction_wheel.speed_sensor': ('adcs','reaction_wheel','subcomponent_sensor'),
 'sun_sensor': ('adcs','sun_sensor','direct_sensor'),
 'battery': ('eps','battery','related_battery_telemetry'),
 'solar_array': ('eps','solar_array','direct_power_array'),
 'propulsion.tank': ('propulsion','propellant_tank','source_known_no_direct_channel'),
 'thermal.radiator': ('thermal','radiator_thermal_control','source_known_no_direct_channel'),
 'comm.transmitter': ('comm','transmitter','source_known_no_direct_channel'),
 'comm.downlink': ('comm','communication_link','related_downlink_telemetry'),
 'imu': ('adcs','imu','source_known_no_direct_channel'),
}



def install_fault_locations(conn:sqlite3.Connection):
    conn.execute('''CREATE TABLE IF NOT EXISTS v2_run_fault_locations(
      source_id INTEGER PRIMARY KEY REFERENCES v2_run_annotations(source_id),
      subsystem TEXT NOT NULL, device TEXT NOT NULL,
      classification_basis TEXT NOT NULL, note TEXT NOT NULL)''')
    rows=conn.execute('SELECT source_id,fault_component FROM v2_run_annotations WHERE run_role=\'FAULT\'').fetchall()
    for sid, original in rows:
        if original not in FAULT_LOCATIONS:
            raise ValueError('缺少经过确认的故障归属映射: '+original)
        subsystem, device, basis=FAULT_LOCATIONS[original]
        conn.execute('INSERT OR IGNORE INTO v2_run_fault_locations VALUES(?,?,?,?,?)',(
          sid,subsystem,device,basis,
          'Fault injection source mapped from targets.component by subsystem engineering semantics; not a claim of fault in all sampled channels.'))
    return len(rows)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);args=p.parse_args()
    with sqlite3.connect(args.database) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        print('Annotated fault origin locations:',install_fault_locations(conn))

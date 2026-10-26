---
id: reciprocating-compressor-rc
title: RC-Series Reciprocating Compressor — Operation and Maintenance Manual
asset_type: compressor
applies_to: RC-2, RC-4
---

> Synthetic manual written for the Maintenance Copilot demo. Not for use on real equipment.

## Overview

RC-series compressors from Meridian Rotating Equipment are two- and four-throw,
double-acting reciprocating compressors used for gas gathering and pipeline service. Gas
passes through a suction scrubber, then the compressor valves, cylinders and an
intercooler between stages. Compressor valves are the most frequent cause of unplanned
downtime.

## Operating limits

| Parameter | Normal | Alarm | Trip |
|---|---|---|---|
| Discharge temperature (any stage) | below 135 °C | 150 °C | 160 °C |
| Frame vibration (velocity, RMS) | below 5 mm/s | 8 mm/s | 12 mm/s |
| Lube oil pressure | 3.5–4.5 bar | 2.5 bar | 2.0 bar |
| Suction scrubber liquid level | below 20 % | 50 % | 75 % |

## Compressor valve failure

Symptoms: loss of capacity, higher discharge temperature on the affected cylinder, and a
change in valve cover temperature compared with neighbouring valves.

Common causes:
- **Liquid carry-over** — liquid droplets in the gas break valve plates and springs. This
  is the most common cause of repeated valve failures.
- **Dirt and debris** — scale or corrosion products from the suction piping.
- **Pulsation** — operating at a speed or load that excites piping pulsation.
- **Wrong spring set** for the current gas composition or pressure.

### Repeat valve failures

Two or more valve failures on the same cylinder within 90 days need a root cause
analysis before the next replacement:
1. Check the suction scrubber: level controller, dump valve, and the drain line for
   blockage. Confirm the high-level alarm works.
2. Check the gas temperature against its dew point at suction. Gas that cools below its
   dew point condenses liquid in the suction line.
3. Inspect the broken valve parts. Plates broken into many pieces point to liquid;
   worn seats and pitting point to dirt.
4. Review the operating speed against the pulsation study.

## High discharge temperature

A rising discharge temperature usually means a leaking valve or a fouled intercooler.

1. Compare valve cover temperatures. One hot valve cover identifies the leaking valve.
2. Check the intercooler: cooling water flow, fan operation and tube fouling. Clean the
   cooler if the approach temperature has risen by more than 10 °C.
3. Check the compression ratio. A higher ratio than design raises discharge temperature.

## Lube oil contamination

Send oil samples for analysis every month. Change oil and filters when water exceeds
500 ppm or particle counts exceed the limits on the lube system nameplate. Repeated water
contamination suggests a cooler leak or condensation in the crankcase breather.

## High vibration alarm

When frame vibration exceeds the 8 mm/s alarm:

1. Check foundation and anchor bolt torque. Loose bolts are the most common cause.
2. Check crosshead and crosshead pin clearances for wear.
3. Check that all cylinders are loaded evenly. An unloaded cylinder unbalances the frame.
4. If vibration reaches 12 mm/s, shut down and inspect before restarting.

## Seal gas and packing leaks

Rod packing leak rates should be measured monthly at the vent. Replace packing rings when
the leak rate doubles from the baseline after the last overhaul.

## Preventive maintenance schedule

| Interval | Task |
|---|---|
| Daily | Log temperatures, pressures and scrubber level |
| Monthly | Oil analysis; valve cover temperature survey; packing vent flow |
| 4,000 hours | Inspect and recondition suction and discharge valves |
| 8,000 hours | Inspect cylinder liners, piston rings and rider bands |
| 24,000 hours | Major overhaul |

## Safety

Depressurise and purge the compressor before opening any valve cover. Lock out the driver
and bar the crankshaft. Gas can be trapped between valves even after blowdown.

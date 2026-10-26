---
id: gas-turbine-ad30
title: AD-30 Aeroderivative Gas Turbine — Operation and Maintenance Manual
asset_type: gas_turbine
applies_to: AD-30
---

> Synthetic manual written for the Maintenance Copilot demo. Not for use on real equipment.

## Overview

The AD-30 from Meridian Rotating Equipment is an aeroderivative gas turbine: a two-spool
engine derived from an aircraft turbofan, adapted to drive a generator or compressor. Air
passes the fan and low-pressure compressor (LPC), then the high-pressure compressor (HPC),
the combustor, and the high- and low-pressure turbines (HPT, LPT). The most common cause of
gradual performance loss is HPC degradation.

## Sensor reference

| Tag | Measurement | Units |
|---|---|---|
| T24 | Total temperature at LPC outlet | °R |
| T30 | Total temperature at HPC outlet | °R |
| T50 | Total temperature at LPT outlet (exhaust) | °R |
| P30 | Total pressure at HPC outlet | psia |
| Ps30 | Static pressure at HPC outlet | psia |
| Nf | Physical fan speed | rpm |
| Nc | Physical core speed | rpm |
| NRf, NRc | Corrected fan and core speed | rpm |
| phi | Ratio of fuel flow to Ps30 | pps/psi |
| BPR | Bypass ratio | — |
| htBleed | Bleed enthalpy | — |
| W31, W32 | HPT and LPT coolant bleed | lbm/s |

## HPC degradation

As the high-pressure compressor degrades, it needs more work to deliver the same pressure,
and the engine runs hotter. The typical signature is:

- **T30 rising** — HPC outlet temperature increases.
- **T50 rising** — exhaust temperature increases; the engine burns more fuel per unit of power.
- **Ps30 rising and P30 falling** — the pressure profile at the HPC outlet changes.
- **phi falling** — fuel flow relative to Ps30 drops.
- **Core and fan speed shifts** (Nc, Nf) — the control system compensates for lost efficiency.

Degradation has two parts. **Fouling** (dirt on the blades) is recoverable by washing.
**Erosion and tip wear** are not recoverable and progress until overhaul. If a turbine
shows these trends together, follow the water wash procedure, then judge recovery.

## Compressor water wash

- **Online wash** — performed at load, weekly in dusty locations. Removes light fouling.
- **Offline wash** — performed on a cold engine at crank speed, with detergent. Removes
  heavier fouling. Allow 8 hours including cool-down.

After an offline wash, compare T30 and T50 with the values before the wash. If less than
half of the lost margin is recovered, the degradation is mostly **non-recoverable**.
Schedule a borescope inspection of HPC stages 3 to 7 and plan a hot section inspection.
Repeated washing will not restore performance in this case.

## Remaining life based maintenance planning

Where a remaining useful life (RUL) estimate is available from condition monitoring, plan
work by status:

| Status | Estimated cycles remaining | Action |
|---|---|---|
| Healthy | 75 or more | Normal monitoring and wash schedule |
| Watch | 30 to 74 | Review trends weekly; perform offline wash; plan borescope at next outage |
| Critical | below 30 | Borescope within 10 cycles; schedule hot section inspection; consider derating load |

Treat the estimate as a range, not a single number. Plan on the pessimistic end: finishing
work early costs less than an in-service failure.

## High vibration alarm

| Parameter | Alarm | Trip |
|---|---|---|
| Bearing vibration (velocity) | 12.7 mm/s (0.5 in/s) | 25.4 mm/s (1.0 in/s) |

When the alarm activates:

1. Confirm on the redundant probe and check whether vibration follows load or speed.
2. Check for recent changes: wash, maintenance, or a load step.
3. Check bearing temperatures and lube oil pressure. Rising bearing temperature with
   vibration indicates bearing distress: reduce load and prepare to shut down.
4. Check accessory gearbox and mounting bolts. Loose mounts are a common cause.
5. A sudden step in vibration can mean blade damage (foreign object damage or a lost
   blade tip). Shut down and borescope before restarting.

If vibration reaches the trip level, the unit trips automatically. Do not restart until
the cause is found.

## EGT spread and fuel nozzles

A wide spread between exhaust thermocouples (more than 30 °C between the hottest and
coldest) usually means clogged or worn fuel nozzles. Inspect and clean the nozzles feeding
the cold sector first.

## Bearing temperature high

Alarm at 120 °C. Check lube oil supply pressure, oil cooler performance and filter
differential pressure before suspecting the bearing itself.

## Inspection intervals

| Interval | Task |
|---|---|
| 4,000 hours | Borescope inspection of compressor and combustor |
| 25,000 hours | Hot section inspection |
| 50,000 hours | Major overhaul |

## Safety

Lock out the starter and fuel system before entering the enclosure. Allow the engine to
cool to below 65 °C before an offline wash or borescope inspection.

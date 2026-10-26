---
id: centrifugal-pump-cp
title: CP-Series Centrifugal Pump — Operation and Maintenance Manual
asset_type: pump
applies_to: CP-150, CP-300
---

> Synthetic manual written for the Maintenance Copilot demo. Not for use on real equipment.

## Overview

CP-series pumps are single-stage, end-suction centrifugal pumps from Meridian Rotating
Equipment, used for cooling water, process water and condensate service. The rotating
assembly is supported by two rolling-element bearings and sealed with a cartridge
mechanical seal. Most failures in service involve the mechanical seal, the bearings, or
operation away from the best efficiency point (BEP).

## Operating limits

| Parameter | Normal | Alarm | Trip |
|---|---|---|---|
| Bearing housing vibration (velocity, RMS) | below 2.8 mm/s | 4.5 mm/s | 7.1 mm/s |
| Bearing temperature | below 75 °C | 85 °C | 95 °C |
| Flow | 70–110 % of BEP | below 50 % of BEP | below 30 % of BEP |
| Suction pressure margin (NPSHa − NPSHr) | above 1.0 m | 0.5 m | — |

Do not run the pump dry or against a closed discharge valve for more than 30 seconds.

## High vibration alarm

When bearing housing vibration exceeds the 4.5 mm/s alarm:

1. Confirm the reading on a second sensor or a portable meter, and check the trend. A step
   change suggests a sudden event (impeller damage, loose part); a slow rise suggests wear.
2. Take a vibration spectrum and identify the dominant frequency:
   - **1× running speed** — imbalance (impeller fouling or damage).
   - **2× running speed, high axial** — shaft misalignment; check coupling alignment.
   - **Bearing defect frequencies** — bearing damage; plan bearing replacement.
   - **Vane-pass frequency or broadband noise** — cavitation or recirculation; check flow
     and suction conditions.
3. Check the operating point. Running below 50 % of BEP causes recirculation and vibration.
4. If vibration reaches 7.1 mm/s, stop the pump and switch to the standby unit.

## Mechanical seal leakage

Small amounts of vapour at the seal are normal. Visible liquid dripping is a failure.

Common causes:
- Abrasive solids in the pumped liquid or in the seal flush, which score the seal faces.
- Dry running or flashing at the seal faces.
- Shaft misalignment or bearing wear causing excessive shaft movement.
- Seal face materials unsuitable for the service.

### Repeat seal failures

Two or more seal failures on the same pump within 90 days must trigger a root cause
analysis. Replacing the seal again without changing anything will not fix the problem.

For **abrasive or silty water** (for example river intakes):
- Upgrade to hard seal faces: silicon carbide against silicon carbide.
- Change the seal flush. The standard Plan 11 flush takes liquid from the pump discharge,
  so it carries the same solids to the seal. Use a Plan 32 clean external flush, or add
  a Plan 31 cyclone separator to remove solids from the flush.
- Inspect and clean the intake strainer, and check the intake screen for damage.
- Record silt found during seal replacement in the work order.

## Bearing failure

Bearings normally last over 25,000 operating hours. Early failure almost always has a cause:

- **Misalignment** — the most common cause. Laser-align the coupling to within 0.05 mm
  offset and 0.05 mm per 100 mm angularity. Check for soft foot before aligning.
- **Lubrication** — too little or too much grease. Over-greasing raises bearing
  temperature. Follow the grease quantity on the bearing housing nameplate.
- **Contamination** — water or dirt in the oil. Check housing seals.

If a bearing fails again within 90 days of replacement, check alignment, shaft run-out
and pipe strain before fitting new bearings.

## Cavitation

Cavitation happens when suction pressure falls low enough for vapour bubbles to form and
collapse on the impeller. Symptoms are a gravel-like noise, fluctuating discharge pressure
and pitting on the impeller eye.

Checks:
1. Measure suction pressure and compare the available margin with the limit above.
2. Clean the suction strainer. Silt and debris build-up is a common cause on river water.
3. Check that the suction valve is fully open and the suction tank level is normal.
4. Reduce flow towards BEP if the pump is running far to the right of the curve.

## Impeller and wear ring wear

Falling discharge head at the same flow shows internal wear. Replace wear rings when the
clearance exceeds twice the new clearance. In abrasive service, inspect the impeller at
every seal replacement.

## Preventive maintenance schedule

| Interval | Task |
|---|---|
| Weekly | Check for leaks, noise and abnormal temperature |
| Monthly | Vibration reading on both bearings |
| 6 months | Re-grease bearings; check alignment |
| 12 months | Performance test against the pump curve |
| 4 years | Overhaul: bearings, seal, wear rings |

## Safety

Isolate, drain and lock out the pump before any work. Confirm zero energy at the motor
breaker. Hot or hazardous liquids may remain in the casing after shutdown.

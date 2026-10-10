# Northwind Logistics Depot Operations Manual

Version 7.0, maintained by Lars Hendriksen, Head of Operations.

## 1. Depot operations

### 1.1 Opening hours

⟦Standard depot opening hours are 05:00 to 22:00, Monday to Saturday.⟧ Sunday operation requires approval from the depot manager and the Head of Operations.

### 1.2 Dock management

#### 1.2.1 Turnaround targets

- ⟦The dock turnaround target is 45 minutes per vehicle.⟧
- ⟦Trucks that wait more than 30 minutes at the gate trigger a yard alert.⟧
- Reefer trucks are always unloaded first.

#### 1.2.2 Dock door assignment

Doors are assigned by the shift lead in the yard management system. Doors 1 to 4 are reserved for cold goods at every depot.

## 2. Safety on the depot floor

### 2.1 Vehicle speed

⟦Forklifts must not exceed 8 km/h inside the warehouse and 12 km/h in the yard.⟧

### 2.2 Personal protective equipment

- Safety shoes and a high-visibility vest are mandatory.
- Hard hats are mandatory in the high-bay racking aisles.
- Cut-resistant gloves are mandatory when opening cartons with a blade.

## 3. Cold chain

### 3.1 Temperature range

⟦Cold goods (SKU NW-COLD-04) must be stored between 2 and 8 degrees C.⟧

### 3.2 Excursion handling

If the temperature is above 8 degrees C, follow these steps:

1. Record the temperature and time in the log.
2. Move the pallet to quarantine bay Q1.
3. Inform the depot manager.

⟦A pallet that has been above 8 degrees C for more than 20 minutes must be quarantined and cannot be shipped.⟧

## 4. System commands

The depot command line tool is called nwctl. Typical commands are:

```bash
nwctl depot status --code D-RTM
nwctl dock assign --door 7 --vehicle NW-T-0417
⟦nwctl yard alert --depot D-HAM --threshold 30⟧
```

### 4.1 Depot shutdown

To shut down a depot in an emergency, run `nwctl depot shutdown --code <depot> --reason <text>`. ⟦A depot shutdown requires confirmation from the depot manager and the on-call operations director.⟧

## 5. Escalation levels

- Level 1: shift lead, response within 10 minutes.
- Level 2: depot manager, response within 30 minutes.
- ⟦Level 3: Head of Operations, response within 2 hours.⟧

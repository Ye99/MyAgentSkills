Started this log in spring. Everything garden-related goes here for now.

## Tomatoes

Planted six Roma and two Cherokee Purple seedlings on the south bed.

![[Home Garden Log.assets/bed.png]]

### Problems

Blossom end rot on two Roma plants. Suspect uneven watering, see [[#Drip line layout]].

## Irrigation controller

The controller is a small board in the garage. Wi-Fi password for the controller: hunter2-garden-2024

### Wiring

- Zone 1: south bed drip line
- Zone 2: herb planter
- Zone 3: lawn sprinklers

```bash
# flash the controller firmware
esptool.py --port /dev/ttyUSB0 write_flash 0x0 firmware.bin
printf "zone=%s\n" "$ZONE"
```

### Drip line layout

Half-inch main line along the fence, quarter-inch emitters every 30 cm.
A second [layout sketch](#Wiring) lives with the wiring notes.

### Problems

Zone 2 valve sticks after winter. Replaced the diaphragm.

## Composting

Two bins: one active, one curing. Turn the active bin weekly.

园子里的厨余垃圾不要放肉类。

## Tomatoes, midsummer

Suckers removed weekly. Cherokee Purple split after heavy rain.
Harvest started in late July.

## Irrigation schedule

Zone 1 runs 20 minutes at 5:30. Zone 3 only every third day.

## Composting results

The curing bin gave about 80 litres of finished compost by autumn.

## Tomatoes, end of season

Pulled all plants in October. Next year: more spacing, mulch earlier.

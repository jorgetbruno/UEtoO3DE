# One-off probe: dump GeometryScript-related names exposed by the unreal module in UE 5.8.
import unreal

names = sorted(n for n in dir(unreal) if "GeometryScript" in n)
out = []
for n in names:
    obj = getattr(unreal, n)
    methods = sorted(m for m in dir(obj) if not m.startswith("_")) if isinstance(obj, type) else []
    out.append(n + " :: " + (", ".join(methods) if methods else type(obj).__name__))

path = r"D:/Gamedev/UEtoO3DE/Tests/ue/results/geometry_api_probe.txt"
with open(path, "w") as f:
    f.write("\n".join(out))
unreal.log("PROBE WROTE %d names" % len(names))

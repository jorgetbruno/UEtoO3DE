# Minimal UE-side smoke test: project loads (with staged UEO3DEExporter binaries), Python plugin works.
import unreal

unreal.log("UEtoO3DE smoke: python alive, engine " + unreal.SystemLibrary.get_engine_version())
unreal.log("UEtoO3DE smoke: project = " + unreal.Paths.get_project_file_path())

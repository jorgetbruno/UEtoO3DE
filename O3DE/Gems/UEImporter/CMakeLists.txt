#
# UEImporter — a Python-only gem. This file exists so that a SOURCE BUILD of a
# project that has the gem registered can configure at all.
#
# Registering the gem adds its root to the project's `external_subdirectories`,
# and O3DE's Subdirectories.cmake calls `add_subdirectory()` on every entry.
# CMake requires a CMakeLists.txt there, so without this file a project that
# imports perfectly well in a prebuilt SDK editor fails to configure:
#
#     CMake Error at cmake/Subdirectories.cmake:497 (add_subdirectory):
#       The source directory
#         .../O3DE/Gems/UEImporter
#       does not contain a CMakeLists.txt file.
#
# The gem has no compiled code -- everything lives in Editor/Scripts and is
# mounted through the Registry entry `install_gem.py` writes -- so there are no
# targets to declare. The body below is the same gem_type dispatch the engine's
# own gems carry (see Gems/Camera/CMakeLists.txt), which for a "Tool" gem does
# nothing. Keeping the shape rather than leaving the file empty means an
# Asset-type variant later behaves the way the engine expects.
#

o3de_read_json_key(GEM_TYPE ${CMAKE_CURRENT_SOURCE_DIR}/gem.json "type")
if (GEM_TYPE STREQUAL "Asset")
    if(LY_MONOLITHIC_GAME)
        include(Platform/${PAL_PLATFORM_NAME}/platform_${PAL_PLATFORM_NAME_LOWERCASE}.cmake OPTIONAL)
    else()
        include(Platform/${PAL_PLATFORM_NAME}/platform_${PAL_PLATFORM_NAME_LOWERCASE}.cmake)
    endif()
endif()

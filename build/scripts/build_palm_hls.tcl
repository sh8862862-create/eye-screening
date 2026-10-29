set package_root [file normalize [file join [file dirname [info script]] ../..]]
set work_dir [file join $package_root build work]
if {[info exists ::env(EYE_BUILD_DIR)]} {set work_dir [file normalize $::env(EYE_BUILD_DIR)]}

set project [file join $work_dir palm_hls]
if {[file exists $project]} {error "Refuse to overwrite existing HLS build"}
open_project $project
set_top palm_depthwise
add_files [file join $package_root src hls palm_depthwise_v6.cpp]
add_files -tb [file join $package_root sim hls test_depthwise.cpp]
open_solution solution1 -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 10 -name default
csim_design
csynth_design
cosim_design -rtl verilog -tool xsim -trace_level none
export_design -rtl verilog -format ip_catalog -vendor user.org -library hls -version 1.0 -output [file join $work_dir palm_ip]
exit

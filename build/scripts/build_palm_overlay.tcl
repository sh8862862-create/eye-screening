set package_root [file normalize [file join [file dirname [info script]] ../..]]
set work_dir [file join $package_root build work]
if {[info exists ::env(EYE_BUILD_DIR)]} {set work_dir [file normalize $::env(EYE_BUILD_DIR)]}
# One PYNQ-Z2 overlay for all three production consumers:
#
#   axi_uartlite @0x41200000   Pmod A pin 1/2  (Y18 TX / Y19 RX)  -> CH9329
#   axi_iic      @0x41600000   Pmod B pin 1/2  (W14 SCL / Y14 SDA) -> SSD1306 OLED
#   axi_gpio     @0x40000000   BTN0..3 (D19 / D20 / L20 / L19), inputs only
#   axi_iic      @0x41800000   Pmod B pin 9/10 (V12 SCL / W13 SDA) -> WonderEcho
#
# Loading base.bit is then never needed for either of them, which removes the
# "another session reloads base.bit and wedges the PS" failure mode.
#
# Button inputs use the AXI GPIO IP's built-in four-stage lib_cdc synchronizer.
# Verified in Vivado 2024.1 axi_gpio_v2_0_vh_rfs.vhd INPUT_DOUBLE_REGS3:
# lib_cdc_v1_0_3.cdc_sync C_CDC_TYPE=1, C_MTBF_STAGES=4. Software debounces them.
# Pin source: sources/pynq_z2_part0_pins.xml from the TUL PYNQ-Z2 A.0 board file.
#
# Usage: vivado -mode batch -source build_shared_overlay.tcl [-tclargs project_dir output_dir]
# Output: out_voice/pynq_shared.bit + .hwh (original project and out are untouched)

set part_name   "xc7z020clg400-1"
set proj_name   "shared_overlay"
set baud_rate   9600
# Measured on the board (PYNQ 3.1.1, 2026-10-20): FCLK0 = 62.5 MHz.
set fclk_mhz    62.5

set uart_base   0x41200000
set iic_base    0x41600000
set voice_iic_base 0x41800000
set buttons_base 0x40000000
set pmoda_tx    "Y18"
set pmoda_rx    "Y19"
set pmodb_scl   "W14"
set pmodb_sda   "Y14"
set voice_scl   "V12"
set voice_sda   "W13"

set script_dir  [file normalize [file dirname [info script]]]
set proj_dir    [file join $work_dir shared_overlay]
set out_dir     [file join $work_dir output]
if {$argc != 0 && $argc != 2} { error "expected zero arguments, or project_dir output_dir" }
if {$argc == 2} {
    set proj_dir [file normalize [lindex $argv 0]]
    set out_dir [file normalize [lindex $argv 1]]
}
if {[file exists $proj_dir]} {
    error "project directory already exists: $proj_dir; choose a new directory to preserve prior builds"
}
file mkdir $out_dir

create_project $proj_name $proj_dir -part $part_name

set_property ip_repo_paths [list [file join $work_dir palm_ip]] [current_project]
update_ip_catalog
create_bd_design "design_1"
current_bd_design "design_1"

create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ $fclk_mhz \
] [get_bd_cells ps7]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic
set_property -dict [list CONFIG.NUM_MI {5} CONFIG.NUM_SI {1}] [get_bd_cells axi_ic]

create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst
set_property -dict [list CONFIG.C_EXT_RST_WIDTH {16}] [get_bd_cells rst]

connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0]  [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins axi_ic/ACLK] \
    [get_bd_pins axi_ic/S00_ACLK] [get_bd_pins ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins rst/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins rst/ext_reset_in]
connect_bd_net [get_bd_pins rst/interconnect_aresetn] [get_bd_pins axi_ic/ARESETN]
# S00_ARESETN must be driven, see the 2026-10-21 hang.
connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins axi_ic/S00_ARESETN]

# ------------------------------------------------------------------- UARTLite
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_uartlite:2.0 uartlite
set_property -dict [list \
    CONFIG.C_BAUDRATE $baud_rate \
    CONFIG.C_DATA_BITS {8} \
    CONFIG.C_USE_PARITY {0} \
    CONFIG.C_S_AXI_ACLK_FREQ_HZ [expr {$fclk_mhz * 1000000}] \
] [get_bd_cells uartlite]

connect_bd_intf_net [get_bd_intf_pins axi_ic/M00_AXI] [get_bd_intf_pins uartlite/S_AXI]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins axi_ic/M00_ACLK] \
    [get_bd_pins uartlite/s_axi_aclk]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] \
    [get_bd_pins axi_ic/M00_ARESETN] [get_bd_pins uartlite/s_axi_aresetn]

create_bd_port -dir O -type data uart_txd
create_bd_port -dir I -type data uart_rxd
connect_bd_net [get_bd_pins uartlite/tx] [get_bd_ports uart_txd]
connect_bd_net [get_bd_pins uartlite/rx] [get_bd_ports uart_rxd]

assign_bd_address -target_address_space /ps7/Data \
    [get_bd_addr_segs uartlite/S_AXI/Reg] -force -offset $uart_base -range 0x10000

# ------------------------------------------------------------------- AXI IIC
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_iic:2.1 iic
set_property -dict [list \
    CONFIG.C_SCL_INERTIAL_DELAY {0} \
    CONFIG.C_SDA_INERTIAL_DELAY {0} \
    CONFIG.IIC_FREQ_KHZ {100} \
    CONFIG.TEN_BIT_ADR {7_bit} \
] [get_bd_cells iic]

connect_bd_intf_net [get_bd_intf_pins axi_ic/M01_AXI] [get_bd_intf_pins iic/S_AXI]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins axi_ic/M01_ACLK] \
    [get_bd_pins iic/s_axi_aclk]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] \
    [get_bd_pins axi_ic/M01_ARESETN] [get_bd_pins iic/s_axi_aresetn]

set iic_intf ""
set intf_pins [get_bd_intf_pins -quiet iic/IIC]
if {[llength $intf_pins]} {
    # axi_iic exposes the bus as an IIC interface, not as raw scl/sda pins.
    create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 iic_rtl
    connect_bd_intf_net [get_bd_intf_pins iic/IIC] [get_bd_intf_ports iic_rtl]
    set iic_intf 1
} else {
    create_bd_port -dir IO -type data iic_scl
    create_bd_port -dir IO -type data iic_sda
    connect_bd_net [get_bd_pins iic/scl] [get_bd_ports iic_scl]
    connect_bd_net [get_bd_pins iic/sda] [get_bd_ports iic_sda]
}

assign_bd_address -target_address_space /ps7/Data \
    [get_bd_addr_segs iic/S_AXI/Reg] -force -offset $iic_base -range 0x10000

# ---------------------------------------------------------- WonderEcho I2C
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_iic:2.1 voice_iic
set_property -dict [list \
    CONFIG.C_SCL_INERTIAL_DELAY {0} \
    CONFIG.C_SDA_INERTIAL_DELAY {0} \
    CONFIG.IIC_FREQ_KHZ {100} \
    CONFIG.TEN_BIT_ADR {7_bit} \
] [get_bd_cells voice_iic]

connect_bd_intf_net [get_bd_intf_pins axi_ic/M03_AXI] [get_bd_intf_pins voice_iic/S_AXI]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins axi_ic/M03_ACLK] \
    [get_bd_pins voice_iic/s_axi_aclk]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] \
    [get_bd_pins axi_ic/M03_ARESETN] [get_bd_pins voice_iic/s_axi_aresetn]

set voice_intf_pins [get_bd_intf_pins -quiet voice_iic/IIC]
if {[llength $voice_intf_pins]} {
    create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 voice_iic_rtl
    connect_bd_intf_net [get_bd_intf_pins voice_iic/IIC] [get_bd_intf_ports voice_iic_rtl]
} else {
    create_bd_port -dir IO -type data voice_iic_scl
    create_bd_port -dir IO -type data voice_iic_sda
    connect_bd_net [get_bd_pins voice_iic/scl] [get_bd_ports voice_iic_scl]
    connect_bd_net [get_bd_pins voice_iic/sda] [get_bd_ports voice_iic_sda]
}

assign_bd_address -target_address_space /ps7/Data \
    [get_bd_addr_segs voice_iic/S_AXI/Reg] -force -offset $voice_iic_base -range 0x10000

# --------------------------------------------------------- Four input buttons
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 buttons
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {4} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_ALL_OUTPUTS {0} \
    CONFIG.C_IS_DUAL {0} \
    CONFIG.C_INTERRUPT_PRESENT {0} \
] [get_bd_cells buttons]
connect_bd_intf_net [get_bd_intf_pins axi_ic/M02_AXI] [get_bd_intf_pins buttons/S_AXI]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins axi_ic/M02_ACLK] \
    [get_bd_pins buttons/s_axi_aclk]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] \
    [get_bd_pins axi_ic/M02_ARESETN] [get_bd_pins buttons/s_axi_aresetn]
create_bd_port -dir I -from 3 -to 0 btn
connect_bd_net [get_bd_ports btn] [get_bd_pins buttons/gpio_io_i]
assign_bd_address -target_address_space /ps7/Data \
    [get_bd_addr_segs buttons/S_AXI/Reg] -force -offset $buttons_base -range 0x10000

# Accelerator on its own 100 MHz clock; peripheral FCLK0 stays 62.5 MHz.
set_property -dict [list CONFIG.PCW_USE_S_AXI_HP0 {1} CONFIG.PCW_EN_CLK1_PORT {1} CONFIG.PCW_EN_RST1_PORT {1} CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ {100}] [get_bd_cells ps7]
create_bd_cell -type ip -vlnv user.org:hls:palm_depthwise:1.0 palm_accel
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 accel_rst
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 accel_ddr
set_property -dict [list CONFIG.NUM_MI {1} CONFIG.NUM_SI {1}] [get_bd_cells accel_ddr]
connect_bd_net [get_bd_pins ps7/FCLK_CLK1] [get_bd_pins accel_rst/slowest_sync_clk] [get_bd_pins palm_accel/ap_clk] [get_bd_pins axi_ic/M04_ACLK] [get_bd_pins accel_ddr/ACLK] [get_bd_pins accel_ddr/S00_ACLK] [get_bd_pins accel_ddr/M00_ACLK] [get_bd_pins ps7/S_AXI_HP0_ACLK]
connect_bd_net [get_bd_pins ps7/FCLK_RESET1_N] [get_bd_pins accel_rst/ext_reset_in]
connect_bd_net [get_bd_pins accel_rst/interconnect_aresetn] [get_bd_pins accel_ddr/ARESETN]
connect_bd_net [get_bd_pins accel_rst/peripheral_aresetn] [get_bd_pins palm_accel/ap_rst_n] [get_bd_pins axi_ic/M04_ARESETN] [get_bd_pins accel_ddr/S00_ARESETN] [get_bd_pins accel_ddr/M00_ARESETN]
connect_bd_intf_net [get_bd_intf_pins axi_ic/M04_AXI] [get_bd_intf_pins palm_accel/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins palm_accel/m_axi_gmem] [get_bd_intf_pins accel_ddr/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins accel_ddr/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]
assign_bd_address -target_address_space /ps7/Data [get_bd_addr_segs palm_accel/s_axi_control/Reg] -force -offset 0x43C10000 -range 0x10000
assign_bd_address -target_address_space [get_bd_addr_spaces palm_accel/Data_m_axi_gmem] [get_bd_addr_segs ps7/S_AXI_HP0/HP0_DDR_LOWOCM]

validate_bd_design

foreach pin {axi_ic/ARESETN axi_ic/S00_ARESETN axi_ic/M00_ARESETN axi_ic/M01_ARESETN axi_ic/M02_ARESETN axi_ic/M03_ARESETN axi_ic/M04_ARESETN accel_ddr/S00_ARESETN accel_ddr/M00_ARESETN} {
    if {[llength [get_bd_nets -quiet -of_objects [get_bd_pins $pin]]] == 0} {
        error "reset pin $pin is not connected - this would hang the board"
    }
}
puts "preflight: all interconnect resets connected"

save_bd_design

generate_target all [get_files design_1.bd]
make_wrapper -files [get_files design_1.bd] -top -import

set wrapper [get_files -quiet *design_1_wrapper.v]
set_property top [file rootname [file tail [lindex $wrapper 0]]] [current_fileset]
update_compile_order -fileset sources_1

# ---------------------------------------------------------------- constraints
# The top-level port names depend on how the IIC is exposed (interface port vs
# raw pins), so read them back from the generated wrapper instead of guessing.
set wrapper_file [lindex $wrapper 0]
set fh [open $wrapper_file r]
set wrapper_text [read $fh]
close $fh
set scl_port ""
set sda_port ""
set voice_scl_port ""
set voice_sda_port ""
foreach line [split $wrapper_text "\n"] {
    if {[regexp {inout\s+(iic\w*scl\w*)} $line -> found]} { set scl_port $found }
    if {[regexp {inout\s+(iic\w*sda\w*)} $line -> found]} { set sda_port $found }
    if {[regexp {inout\s+(voice\w*scl\w*)} $line -> found]} { set voice_scl_port $found }
    if {[regexp {inout\s+(voice\w*sda\w*)} $line -> found]} { set voice_sda_port $found }
}
if {$scl_port eq "" || $sda_port eq "" || $voice_scl_port eq "" || $voice_sda_port eq ""} {
    error "could not find both I2C port pairs in $wrapper_file"
}
puts "preflight: IIC ports are $scl_port / $sda_port"

set xdc [file join $proj_dir "${proj_name}.srcs" "constrs_1" "pynq_shared.xdc"]
file mkdir [file dirname $xdc]
set fh [open $xdc w]
puts $fh "set_property CFGBVS VCCO \[current_design\]"
puts $fh "set_property CONFIG_VOLTAGE 3.3 \[current_design\]"
puts $fh "set_property -dict { PACKAGE_PIN $pmoda_tx IOSTANDARD LVCMOS33 } \[get_ports uart_txd\]"
puts $fh "set_property -dict { PACKAGE_PIN $pmoda_rx IOSTANDARD LVCMOS33 } \[get_ports uart_rxd\]"
puts $fh "set_property -dict { PACKAGE_PIN $pmodb_scl IOSTANDARD LVCMOS33 PULLUP true } \[get_ports $scl_port\]"
puts $fh "set_property -dict { PACKAGE_PIN $pmodb_sda IOSTANDARD LVCMOS33 PULLUP true } \[get_ports $sda_port\]"
puts $fh "set_property -dict { PACKAGE_PIN $voice_scl IOSTANDARD LVCMOS33 PULLUP true } \[get_ports $voice_scl_port\]"
puts $fh "set_property -dict { PACKAGE_PIN $voice_sda IOSTANDARD LVCMOS33 PULLUP true } \[get_ports $voice_sda_port\]"
foreach idx {0 1 2 3} pin {D19 D20 L20 L19} {
    puts $fh "set_property -dict { PACKAGE_PIN $pin IOSTANDARD LVCMOS33 } \[get_ports {btn\[$idx\]}\]"
}
close $fh
add_files -fileset constrs_1 -norecurse $xdc

launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} { error "synth failed" }

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} { error "impl failed" }

set bit_src [file join $proj_dir "${proj_name}.runs" "impl_1" "design_1_wrapper.bit"]
set hwh_src [file join $proj_dir "${proj_name}.gen" "sources_1" "bd" "design_1" "hw_handoff" "design_1.hwh"]
open_run impl_1
report_drc -file [file join $out_dir "shared_drc.rpt"]
report_timing_summary -report_unconstrained -file [file join $out_dir "shared_timing.rpt"]
report_bus_skew -file [file join $out_dir "shared_bus_skew.rpt"]
report_cdc -file [file join $out_dir "shared_cdc.rpt"]
report_utilization -file [file join $out_dir "shared_utilization.rpt"]
set timing_paths [get_timing_paths -quiet -max_paths 1 -nworst 1]
if {[llength $timing_paths] == 0} { error "no constrained timing paths" }
if {[get_property SLACK [lindex $timing_paths 0]] < 0} { error "setup timing failed" }
set drc_errors [get_drc_violations -quiet -filter {SEVERITY == Error}]
if {[llength $drc_errors]} { error "DRC errors: $drc_errors" }

file copy -force $bit_src [file join $out_dir "pynq_shared.bit"]
file copy -force $hwh_src [file join $out_dir "pynq_shared.hwh"]
file copy -force $xdc       [file join $out_dir "pynq_shared.xdc"]

puts "SHARED_BUILD_OK"

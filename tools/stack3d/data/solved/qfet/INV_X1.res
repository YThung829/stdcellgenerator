** Objective value: 999.0
** CPP cost (col idx): 1

** Placement Result **
Name      X     Y    Z  Flip  Width  Height  SrcCol  SrcNet  DrnCol  DrnNet  GCol  GNet  Model
----------------------------------------------------------------------------------------------
MM0S0  42.0   0.0  PC1    NF   84.0    48.0    42.0      ZN      -1     VSS  84.0     I   NMOS
MM1S0  42.0  96.0  PC1    NF   84.0    48.0    42.0      ZN      -1     VDD  84.0     I   PMOS

** Cell Information **
IO Pins
----------------------
I ZN

** Routing Result **
MET  ROW  COL  NET      MET  ROW  COL  NET
------------------------------------------
5      0   84    I  =>  6      0   84    I
5     48   42   ZN  =>  5     96   42   ZN
5     96   42   ZN  =>  6     96   42   ZN
6      0   84    I  =>  6      0  126    I
6      0  126    I  =>  7      0  126    I
6     48  126    I  =>  7     48  126    I
7      0  126    I  =>  7     48  126    I

** Technology Parameters **
Name                               Value
------------------------------------------
COL                                    2
TRACK                                  4
NUM_FIN                                2
NUM_SITES                              1
TECHNOLOGY                          QFET
LIB_NAME                  PROBE3_QFET_2F_4T_4242OF21
HEIGHT_CONFIG                         SH
DIFF_BREAK_TYPE                      SDB
PWR_CONFIG                         M0BPR
PWR_RAIL_THICKNESS_nm               36.0
M0_PITCH                            24.0
DEFAULT_PLC_LAYER                    PC1
PLACEMENT_LAYERS                BPC1,PC1
PIN_ACCESS_LAYERS                 BM0,M0
PIN_LAYER                         BM0,M0
BPC1_PITCH                          42.0
BPC1_WIDTH                          16.0
PC1_PITCH                           42.0
PC1_WIDTH                           16.0
BM0_PITCH                           24.0
BM0_WIDTH                           14.0
M0_WIDTH                            14.0

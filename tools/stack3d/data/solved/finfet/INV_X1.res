** Objective value: 1021.0
** CPP cost (col idx): 1

** Placement Result **
Name      X     Y  Flip  Width  Height  SrcCol  SrcNet  DrnCol  DrnNet  GCol  GNet  Model
-----------------------------------------------------------------------------------------
MM0S0  45.0   0.0    NF   90.0    48.0    45.0      ZN      -1     VSS  90.0     I   NMOS
MM1S0  45.0  96.0    NF   90.0    48.0    45.0      ZN      -1     VDD  90.0     I   PMOS

** Cell Information **
IO Pins
----------------------
I ZN

** Routing Result **
MET  ROW  COL  NET      MET  ROW  COL  NET
------------------------------------------
0      0   90    I  =>  1      0   90    I
0     48   45   ZN  =>  1     48   45   ZN
1      0   90    I  =>  1      0  120    I
1      0  120    I  =>  2      0  120    I
1     48   45   ZN  =>  1     48   90   ZN
1     48   60   ZN  =>  2     48   60   ZN
2      0   60   ZN  =>  2    144   60   ZN
2      0  120    I  =>  2    144  120    I

** Technology Parameters **
Name                               Value
------------------------------------------
COL                                    2
TRACK                                  4
CPP                                 45.0
M0P                                 24.0
M1P                                 30.0
M2P                                 24.0
CP_WIDTH                            16.0
M0_WIDTH                            14.0
M1_WIDTH                            14.0
M2_WIDTH                            14.0
ACTIVE_GAP                          14.0
PWR_RAIL_THICKNESS                  36.0
PWR_CONFIG                         M0BPR

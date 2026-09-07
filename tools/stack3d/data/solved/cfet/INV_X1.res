** Objective value: 1034.0
** CPP cost (col idx): 1

** Placement Result **
Name      X    Y  Flip  Width  Height  SrcRow  SrcCol  SrcNet  DrnRow  DrnCol  DrnNet   GRow  GCol  GNet  Model
---------------------------------------------------------------------------------------------------------------
MM0S0  45.0  0.0     F   90.0   144.0     0.0   135.0      ZN    -1.0    -1.0     VSS  144.0  90.0     I   NMOS
MM1S0  45.0  0.0     F   90.0   144.0    48.0   135.0      ZN    -1.0    -1.0     VDD   96.0  90.0     I   PMOS

** Cell Information **
IO Pins
----------------------
I ZN

** Routing Result **
MET  ROW  COL  NET      MET  ROW  COL  NET  VIA
-----------------------------------------------
0      0  135   ZN  =>  1      0  135   ZN  MIV
0    144   90    I  =>  1    144   90    I  MIV
1      0  135   ZN  =>  1     48  135   ZN     
1      0  135   ZN  =>  2      0  135   ZN  VIA
1     96   90    I  =>  1    144   90    I     
1     96   90    I  =>  2     96   90    I  VIA
2      0   90   ZN  =>  2      0  135   ZN     
2      0  120   ZN  =>  3      0  120   ZN  VIA
2     96   60    I  =>  2     96   90    I     
2     96   60    I  =>  3     96   60    I  VIA
3      0   60    I  =>  3    144   60    I     
3      0  120   ZN  =>  3    144  120   ZN     

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
M0_PWR_RAIL_THICKNESS               36.0
PWR_CONFIG                         M0BPR

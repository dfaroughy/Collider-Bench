#include "SimpleAnalysis/AnalysisClass.h"
#include "xAODRootAccess/TEvent.h"
#include "xAODRootAccess/TActiveStore.h"
#include "xAODRootAccess/TStore.h"

DefineAnalysis(ZeroLeptonBDT2018)

void ZeroLeptonBDT2018::Init()
{
  // BDT based SR's
  addRegions({"BDTSR_GGd1","BDTSR_GGd2","BDTSR_GGd3","BDTSR_GGd4","BDTSR_GGo1","BDTSR_GGo2","BDTSR_GGo3","BDTSR_GGo4","BDTCRT_GGd1","BDTCRT_GGd2","BDTCRT_GGd3","BDTCRT_GGd4","BDTCRT_GGo1","BDTCRT_GGo2","BDTCRT_GGo3","BDTCRT_GGo4"});

  // Initialize BDT readers
  const std::string xmlFiles[8][2] = {
    // SRBDT-GGdirect
    {"ZeroLepton2018-SRBDT-GGd1_weight1.xml","ZeroLepton2018-SRBDT-GGd1_weight2.xml"},
    {"ZeroLepton2018-SRBDT-GGd2_weight1.xml","ZeroLepton2018-SRBDT-GGd2_weight2.xml"},
    {"ZeroLepton2018-SRBDT-GGd3_weight1.xml","ZeroLepton2018-SRBDT-GGd3_weight2.xml"},
    {"ZeroLepton2018-SRBDT-GGd4_weight1.xml","ZeroLepton2018-SRBDT-GGd4_weight2.xml"},
    // SRBDT-GGonestep
    {"ZeroLepton2018-SRBDT-GGo1_weight1.xml","ZeroLepton2018-SRBDT-GGo1_weight2.xml"},
    {"ZeroLepton2018-SRBDT-GGo2_weight1.xml","ZeroLepton2018-SRBDT-GGo2_weight2.xml"},
    {"ZeroLepton2018-SRBDT-GGo3_weight1.xml","ZeroLepton2018-SRBDT-GGo3_weight2.xml"},
    {"ZeroLepton2018-SRBDT-GGo4_weight1.xml","ZeroLepton2018-SRBDT-GGo4_weight2.xml"},
  };
  // Input variables for each BDTs {"label","definition"}
  const std::vector< std::vector< std::string > >  variableDefs {
    {
      //GGd1
      { "met","meff","Ap",
        "jetPt0","jetPt1","jetPt2","jetPt3",
	 "jetEta0","jetEta1","jetEta2","jetEta3"
      },
      // GGd2
      { "meff","Ap",
        "jetPt0","jetPt1","jetPt2","jetPt3",
	 "jetEta0","jetEta1","jetEta2","jetEta3"
      },
      //GGd3
      { "met","meff","Ap",
        "jetPt0","jetPt1","jetPt2","jetPt3",
	 "jetEta0","jetEta1","jetEta2","jetEta3"
      },
      //GGd4
      { "met","meff",
        "jetPt0","jetPt1","jetPt2","jetPt3",
	 "jetEta0","jetEta1","jetEta2","jetEta3"
      },
     // GGo1
      { "meff","Ap",
        "jetPt0","jetPt1","jetPt2","jetPt3","jetPt4",
	"jetEta0","jetEta1","jetEta2","jetEta3","jetEta4"
      },
      //GGo2
      { "meff","Ap",
        "jetPt0","jetPt1","jetPt2","jetPt3",
	 "jetEta0","jetEta1","jetEta2","jetEta3"
      },
     // GGo3
      { "meff","Ap",
        "jetPt0","jetPt1","jetPt2","jetPt4","jetPt5",
	"jetEta0","jetEta1","jetEta2","jetEta4","jetEta5"
      },
      //GGo4
      { "meff","met",
        "jetPt0","jetPt1","jetPt2","jetPt3",
	 "jetEta0","jetEta1","jetEta2","jetEta3"
      },
    }
  };

  const std::string methodname = "BDTG";
  for( unsigned int i=0 ; i<sizeof(xmlFiles)/sizeof(xmlFiles[0]) ; i++ ){
    BDT::BDTReader* reader = new BDT::BDTReader(methodname,variableDefs[i],xmlFiles[i][0],xmlFiles[i][1]);
    m_BDTReaders.push_back(reader);
  }

}

void ZeroLeptonBDT2018::ProcessEvent(AnalysisEvent *event)
{
  auto electrons  = event->getElectrons(7, 2.47, ELooseLH);
  auto muons      = event->getMuons(6, 2.7, MuMedium);
  auto jets       = event->getJets(20., 2.8);
  auto metVec     = event->getMET();
  double met      = metVec.Et();
  int OverlapVeto = event->getMCVeto();
  //sample overlap removal
  //xAOD::TStore* store = xAOD::TActiveStore::store();
  //unsigned int* pveto = 0;
  //int OverlapVeto=0;
  //if ( !store->retrieve<unsigned int>(pveto,"mcVetoCode").isSuccess() ) throw std::runtime_error("could not retrieve mcVetoCode");
  //  OverlapVeto = *pveto;

  // Reject events with bad jets
  if (countObjects(jets, 20, 2.8, NOT(LooseBadJet))!=0) return;


  // Standard SUSY overlap removal
  jets       = overlapRemoval(jets, electrons, 0.2);
  electrons  = overlapRemoval(electrons, jets, 0.4);
  muons      = overlapRemoval(muons, jets, 0.4);

  // Define signal lepton and bjet
  auto signalMuons      = filterObjects(muons, 6, 2.7, MuD0Sigma3 | MuZ05mm | MuIsoFixedCutTightTrackOnly);
  auto signalElectrons  = filterObjects(electrons, 7, 2.47, ETightLH | ED0Sigma5 | EZ05mm | EIsoFixedCutTight);
  auto signalMuons1     = filterObjects(muons, 50, 2.7, MuD0Sigma3 | MuZ05mm | MuIsoFixedCutTightTrackOnly);
  auto signalElectrons1 = filterObjects(electrons, 50, 2.47, ETightLH | ED0Sigma5 | EZ05mm | EIsoFixedCutTight);

  auto goodJets        = filterObjects(jets, 50);
  auto bjets           = filterObjects(goodJets, 50, 2.5, BTag77MV2c20);


  // baseline lepton veto
  auto leptons        = electrons + muons;
  auto signalleptons  = signalElectrons + signalMuons;
  auto signalleptons1 = signalElectrons1 + signalMuons1;

  // preselection: SR and CRT
  if ((leptons.size() == 0 || signalleptons.size() == 1)){}
  else
    return;

  auto corrected_jets = (leptons.size() == 0) ? goodJets : goodJets + signalleptons1;

  // Define MeffIncl
  float meffIncl = sumObjectsPt(corrected_jets) + met;

  // Define Njets
  int Njets = corrected_jets.size();


  // preselection
  if(met < 300) return;
  if(corrected_jets.size() < 2) return;
  if(corrected_jets[0].Pt() < 200.) return;
  if(corrected_jets[1].Pt() < 50.) return;
  if(meffIncl < 800) return;


  // Meff based analysis regions and selections
  float meff[7] = {};
  for(int nJet=2; nJet<=6; nJet++)
    meff[nJet] = sumObjectsPt(corrected_jets, nJet) + met;

  float dphiMin3    = minDphi(metVec, corrected_jets, 3);
  float dphiMinRest = minDphi(metVec, corrected_jets);
  float Ap          = aplanarity(corrected_jets);
  float Sp          = sphericity(corrected_jets);

  float mT = 0.0;
  if ( signalleptons.size() == 1 )
    mT = calcMT(signalleptons[0], metVec);

  int NLeps = leptons.size();
  int NSigLeps = signalleptons.size();
  int Nbjets = bjets.size();


  ntupVar("NLeps", NLeps);
  ntupVar("NSigLeps", NSigLeps);
  ntupVar("met", met);
  ntupVar("meff", meffIncl);
  ntupVar("Ap", Ap);
  ntupVar("nJet",Njets);
  ntupVar("mT", mT);
  ntupVar("veto",OverlapVeto);
  if (Njets >= 1 )
    ntupVar("jetPt0", corrected_jets[0].Pt());
  if (Njets >= 2 )
    ntupVar("jetPt1", corrected_jets[1].Pt());
  if (Njets >= 3 )
    ntupVar("jetPt2", corrected_jets[2].Pt());
  if (Njets >= 4 )
    ntupVar("jetPt3", corrected_jets[3].Pt());
  if (Njets >= 5)
    ntupVar("jetPt4", corrected_jets[4].Pt());
  if (Njets >= 6)
    ntupVar("jetPt5", corrected_jets[5].Pt());

  if (Njets >= 1 )
    ntupVar("jetEta0",corrected_jets[0].Eta());
  else
    ntupVar("jetEta0",-999.0);
  if (Njets >= 2 )
    ntupVar("jetEta1",corrected_jets[1].Eta());
  else
    ntupVar("jetEta1",-999.0);
  if (Njets >= 3 )
    ntupVar("jetEta2",corrected_jets[2].Eta());
  else
    ntupVar("jetEta2",-999.0);
  if (Njets >= 4 )
    ntupVar("jetEta3",corrected_jets[3].Eta());
  else
    ntupVar("jetEta3",-999.0);
  if (Njets >= 5 )
    ntupVar("jetEta4",corrected_jets[4].Eta());
  else
    ntupVar("jetEta4",-999.0);
  if (Njets >= 6 )
    ntupVar("jetEta5",corrected_jets[5].Eta());
  else
    ntupVar("jetEta5",-999.0);

  ntupVar("dPhi",dphiMin3);
  ntupVar("dPhiR",dphiMinRest);
  ntupVar("metovermeff3",met/meff[4]);
  ntupVar("metovermeff5",met/meff[6]);
  ntupVar("nBJet",Nbjets);


  BDT::BDTReader *reader_A10 = m_BDTReaders[0];
  BDT::BDTReader *reader_A20 = m_BDTReaders[1];
  BDT::BDTReader *reader_A30 = m_BDTReaders[2];
  BDT::BDTReader *reader_A40 = m_BDTReaders[3];
  BDT::BDTReader *reader_B10 = m_BDTReaders[4];
  BDT::BDTReader *reader_B20 = m_BDTReaders[5];
  BDT::BDTReader *reader_B30 = m_BDTReaders[6];
  BDT::BDTReader *reader_B40 = m_BDTReaders[7];

  // GGd1
  float BDTGA10 = -2.0;
  float BDTGA20 = -2.0;
  float BDTGA30 = -2.0;
  float BDTGA40 = -2.0;
  float BDTGB10 = -2.0;
  float BDTGB20 = -2.0;
  float BDTGB30 = -2.0;
  float BDTGB40 = -2.0;

  if (Njets >= 4){
    reader_A10->initInEventLoop();
    reader_A10->setValue("met"    ,met);
    reader_A10->setValue("meff"   ,meffIncl);
    reader_A10->setValue("Ap"     ,Ap           );
    reader_A10->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_A10->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_A10->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_A10->setValue("jetPt3" ,corrected_jets[3].Pt());
    reader_A10->setValue("jetEta0",corrected_jets[0].Eta());
    reader_A10->setValue("jetEta1",corrected_jets[1].Eta());
    reader_A10->setValue("jetEta2",corrected_jets[2].Eta());
    reader_A10->setValue("jetEta3",corrected_jets[3].Eta());
    BDTGA10 = reader_A10->getBDT();
  }
  // GGd2
  if (Njets >= 4){
    reader_A20->initInEventLoop();
    reader_A20->setValue("meff"   ,meffIncl);
    reader_A20->setValue("Ap"     ,Ap           );
    reader_A20->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_A20->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_A20->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_A20->setValue("jetPt3" ,corrected_jets[3].Pt());
    reader_A20->setValue("jetEta0",corrected_jets[0].Eta());
    reader_A20->setValue("jetEta1",corrected_jets[1].Eta());
    reader_A20->setValue("jetEta2",corrected_jets[2].Eta());
    reader_A20->setValue("jetEta3",corrected_jets[3].Eta());
    BDTGA20 = reader_A20->getBDT();
  }
  // GGd3
  if (Njets >= 4){
    reader_A30->initInEventLoop();
    reader_A30->setValue("met"    ,met);
    reader_A30->setValue("meff"   ,meffIncl);
    reader_A30->setValue("Ap"     ,Ap           );
    reader_A30->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_A30->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_A30->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_A30->setValue("jetPt3" ,corrected_jets[3].Pt());
    reader_A30->setValue("jetEta0",corrected_jets[0].Eta());
    reader_A30->setValue("jetEta1",corrected_jets[1].Eta());
    reader_A30->setValue("jetEta2",corrected_jets[2].Eta());
    reader_A30->setValue("jetEta3",corrected_jets[3].Eta());
    BDTGA30 = reader_A30->getBDT();
  }
  // GGd4
  if (Njets >= 4){
    reader_A40->initInEventLoop();
    reader_A40->setValue("met"    ,met);
    reader_A40->setValue("meff"   ,meffIncl);
    reader_A40->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_A40->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_A40->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_A40->setValue("jetPt3" ,corrected_jets[3].Pt());
    reader_A40->setValue("jetEta0",corrected_jets[0].Eta());
    reader_A40->setValue("jetEta1",corrected_jets[1].Eta());
    reader_A40->setValue("jetEta2",corrected_jets[2].Eta());
    reader_A40->setValue("jetEta3",corrected_jets[3].Eta());
    BDTGA40 = reader_A40->getBDT();
  }
  // GGo1

  if (Njets >= 5 ){
    reader_B10->initInEventLoop();
    reader_B10->setValue("meff"   ,meffIncl);
    reader_B10->setValue("Ap"     ,Ap           );
    reader_B10->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_B10->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_B10->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_B10->setValue("jetPt3" ,corrected_jets[3].Pt());
    reader_B10->setValue("jetPt4" ,corrected_jets[4].Pt());
    reader_B10->setValue("jetEta0",corrected_jets[0].Eta());
    reader_B10->setValue("jetEta1",corrected_jets[1].Eta());
    reader_B10->setValue("jetEta2",corrected_jets[2].Eta());
    reader_B10->setValue("jetEta3",corrected_jets[3].Eta());
    reader_B10->setValue("jetEta4",corrected_jets[4].Eta());
    BDTGB10 = reader_B10->getBDT();
  }
  // GGo2
  if (Njets >= 4 ){
    reader_B20->initInEventLoop();
    reader_B20->setValue("meff"   ,meffIncl);
    reader_B20->setValue("Ap"     ,Ap           );
    reader_B20->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_B20->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_B20->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_B20->setValue("jetPt3" ,corrected_jets[3].Pt());
    reader_B20->setValue("jetEta0",corrected_jets[0].Eta());
    reader_B20->setValue("jetEta1",corrected_jets[1].Eta());
    reader_B20->setValue("jetEta2",corrected_jets[2].Eta());
    reader_B20->setValue("jetEta3",corrected_jets[3].Eta());
    BDTGB20 = reader_B20->getBDT();
  }
  // GGo3
  if (Njets >= 6 ){
    reader_B30->initInEventLoop();
    reader_B30->setValue("meff"   ,meffIncl);
    reader_B30->setValue("Ap"     ,Ap           );
    reader_B30->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_B30->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_B30->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_B30->setValue("jetPt4" ,corrected_jets[4].Pt());
    reader_B30->setValue("jetPt5" ,corrected_jets[5].Pt());
    reader_B30->setValue("jetEta0",corrected_jets[0].Eta());
    reader_B30->setValue("jetEta1",corrected_jets[1].Eta());
    reader_B30->setValue("jetEta2",corrected_jets[2].Eta());
    reader_B30->setValue("jetEta4",corrected_jets[4].Eta());
    reader_B30->setValue("jetEta5",corrected_jets[5].Eta());
    BDTGB30 = reader_B30->getBDT();
  }
  // GGo4
  if (Njets >= 4 ){
    reader_B40->initInEventLoop();
    reader_B40->setValue("meff"   ,meffIncl);
    reader_B40->setValue("met"    ,met     );
    reader_B40->setValue("jetPt0" ,corrected_jets[0].Pt());
    reader_B40->setValue("jetPt1" ,corrected_jets[1].Pt());
    reader_B40->setValue("jetPt2" ,corrected_jets[2].Pt());
    reader_B40->setValue("jetPt3" ,corrected_jets[3].Pt());
    reader_B40->setValue("jetEta0",corrected_jets[0].Eta());
    reader_B40->setValue("jetEta1",corrected_jets[1].Eta());
    reader_B40->setValue("jetEta2",corrected_jets[2].Eta());
    reader_B40->setValue("jetEta3",corrected_jets[3].Eta());
    BDTGB40 = reader_B40->getBDT();
  }


  // Store BDT score variable
  ntupVar("BDTGA10", BDTGA10);
  ntupVar("BDTGA20", BDTGA20);
  ntupVar("BDTGA30", BDTGA30);
  ntupVar("BDTGA40", BDTGA40);
  ntupVar("BDTGB10", BDTGB10);
  ntupVar("BDTGB20", BDTGB20);
  ntupVar("BDTGB30", BDTGB30);
  ntupVar("BDTGB40", BDTGB40);

//  // SR definition
 if ( OverlapVeto==0 && leptons.size()  == 0 ){
   // GGd1
   if( Njets >= 4 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
	dphiMin3 > 0.4 && dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA10 >= 0.97 )
     accept("BDTSR_GGd1");

   // GGd2
   if( Njets >= 4 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
	dphiMin3 > 0.4 && dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA20 >= 0.94 )
     accept("BDTSR_GGd2");

   // GGd3
   if( Njets >= 4 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
	dphiMin3 > 0.4 && dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA30 >= 0.94 )
     accept("BDTSR_GGd3");

   // GGd4
   if( Njets >= 4 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
	dphiMin3 > 0.4 && dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA40 >= 0.87 )
     accept("BDTSR_GGd4");

   // GGo1
   if( Njets >= 6 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[5].Pt() > 50. &&
	dphiMin3 > 0.4 && dphiMinRest > 0.4 && met/meff[6] > 0.2 && BDTGB10 >= 0.96 )
     accept("BDTSR_GGo1");

   // GGo2
   if( Njets >= 6 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[5].Pt() > 50. &&
	dphiMin3 > 0.4 && dphiMinRest > 0.4 && met/meff[6] > 0.2 && BDTGB20 >= 0.87 )
     accept("BDTSR_GGo2");

   // GGo3
   if( Njets >= 5 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[4].Pt() > 50. &&
	dphiMin3 > 0.4 && dphiMinRest > 0.4 && met/meff[6] > 0.2 && BDTGB30 >= 0.92 )
     accept("BDTSR_GGo3");

   // GGo4
   if( Njets >= 5 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[4].Pt() > 50. &&
	dphiMin3 > 0.2 && dphiMinRest > 0.2 && met/meff[6] > 0.2 && BDTGB40 >= 0.84 )
     accept("BDTSR_GGo4");
 }
//
//  // CRT definition
//  if ( OverlapVeto==0 && signalleptons.size()  == 1 && mT > 30.0 && mT < 100.0 && bjets.size() >= 1 ){
//    // GGd1
//    if( Njets >= 4 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
//	dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA10 >= 0.97 )
//      accept("BDTCRT_GGd1");
//
//    // GGd2
//    if( Njets >= 4 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
//	dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA20 >= 0.94 )
//      accept("BDTCRT_GGd2");
//
//    // GGd3
//    if( Njets >= 4 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
//	dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA30 >= 0.94 )
//      accept("BDTCRT_GGd3");
//
//    // GGd4
//    if( Njets >= 4 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[3].Pt() > 50. &&
//	dphiMinRest > 0.4 && met/meff[4] > 0.2 && BDTGA40 >= 0.89 )
//      accept("BDTCRT_GGd4");
//
//    // GGo1
//    if( Njets >= 6 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[5].Pt() > 50. &&
//	met/meff[6] > 0.2 && BDTGB10 >= 0.96 )
//      accept("BDTCRT_GGo1");
//
//    // GGo2
//    if( Njets >= 6 && meffIncl > 1400. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[5].Pt() > 50. &&
//	met/meff[6] > 0.2 && BDTGB20 >= 0.87 )
//      accept("BDTCRT_GGo2");
//
//    // GGo3
//    if( Njets >= 5 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[4].Pt() > 50. &&
//	met/meff[6] > 0.2 && BDTGB30 >= 0.92 )
//      accept("BDTCRT_GGo3");
//
//    // GGo4
//    if( Njets >= 5 && meffIncl > 800. && met > 300. && corrected_jets[0].Pt() > 200. && corrected_jets[4].Pt() > 50. &&
//	met/meff[6] > 0.2 && BDTGB40 >= 0.84 )
//      accept("BDTCRT_GGo4");
//  }


  return;
}

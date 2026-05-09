// main144.cc is a part of the PYTHIA event generator.
// Copyright (C) 2025 Torbjorn Sjostrand.
// PYTHIA is licenced under the GNU GPL v2 or later, see COPYING for details.
// Please respect the MCnet Guidelines, see GUIDELINES for details.

// Authors: Christian Bierlich <christian.bierlich@fysik.lu.se>

// Keywords: analysis; hepmc; command file; command line option; root; rivet;
//           tuning

// Streamlined event generation with possibility to output ROOT files,
// output HepMC files and run RIVET analyses, all by specifying output modes
// in a cmnd file, where also the event generator settings are specified.
// The example is run with command line options, run ./main144 -h to see a
// full list. See ROOT Usage for information about ROOT output, RIVET Usage
// for information about RIVET and HepMC Interface for information about HepMC.

#include "Pythia8/Pythia.h"
#include "Pythia8/HeavyIons.h"
#include "Pythia8Plugins/Pythia8Rivet.h"
#include "Pythia8Plugins/InputParser.h"
#include "main144.h"
#include <chrono>
#ifdef PY8ROOT
#include "TSystem.h"
#include "TTree.h"
#include "TFile.h"
#endif

using namespace Pythia8;

int main(int argc, char* argv[]) {

  // Parser object for command line input.
  InputParser ip("Run Pythia with cmnd file input, and get Rivet, HepMC or"
    " standard Pythia output.",
    {"./main144 [options]", "./main144 -c main144.cmnd -n 1000 -o myoutput"},
    "Additional options in cmnd file:\n"
    "\tMain:runRivet = on \n\t\tRun Rivet analyses (requires a\n"
    "\t\tworking installation of Rivet, linked to main144).\n"
    "\tMain:analyses = ANALYSIS1,ANALYSIS2,...\n "
    "\t\tA comma separated list of desired Rivet analyses to be run.\n"
    "\t\tAnalyses can be post-fixed with Rivet analysis parameters:\n"
    "\t\tANALYSIS:parm->value:parm2->value2 etc.\n"
    "\tMain:rivetRunName = STRING \n\t\tAdd an optional run name to "
    "the Rivet analysis.\n"
    "\tMain:rivetIgnoreBeams = on\n\t\tIgnore beams in Rivet. \n"
    "\tMain:rivetDumpPeriod = NUMBER\n\t\tDump Rivet histograms "
    "to file evert NUMBER of events.\n"
    "\tMain:rivetDumpFile = STRING\n\t\t Specify alternative "
    "name for Rivet dump file. Default = OUT.\n"
    "\tMain:writeHepMC = on \n\t\tWrite HepMC output (requires "
    "a linked installation of HepMC).\n"
    "\tMain:writeRoot = on \n\t\tWrite a root tree defined in the "
    "main144.h header file.\n\t\tRequires a working installation of Root, "
    "linked to Pythia.\n"
    "\tMain:outputLog = on\n\t\tRedirect output to a logfile. Default is "
    "OUT prefix, i.e., pythia.log.\n");

  // Set up command line options.
  ip.require("c", "Use this user-written command file.", {"-cmnd"});
  ip.add("c2", "", "Use a second cmnd file, loaded after the first.",
    {"-cmnd2"});
  ip.add("s", "-1", "Specify seed for the random number generator.",
    {"-seed"});
  ip.require("o", "Specify output filenames for Rivet (.yoda), log-file etc.",
    {"-out"});
  ip.add("n", "-1", "Number of events. Overrides cmnd.file.", {"-nevents"});
  ip.add("l", "false", "Silence the splash screen.");
  ip.add("t", "false", "Time event generation.", {"-time"});
  ip.add("v", "false", "Print Pythia version number and exit.", {"-version"});

  // Initialize the parser and exit if necessary.
  InputParser::Status status = ip.init(argc, argv);
  if (status != InputParser::Valid) return status;

  // Print version number and exit.
  if (ip.get<bool>("v")) {
    cout << "PYTHIA version: " << PYTHIA_VERSION << endl;
    return 0;
  }

  string cmndfile = ip.get<string>("c");
  if (cmndfile.find(".cmnd") == string::npos &&
    cmndfile.find(".dat") == string::npos) {
    cout << "Please provide a valid .cmnd file as "
      "argument to the -c option." << endl;
    return 1;
  }

  string cmndfile2 = ip.get<string>("c2");
  // Optional secondary input command file.
  if(cmndfile2 != "" && cmndfile2.find(".cmnd") == string::npos &&
    cmndfile2.find(".dat") == string::npos) {
    cout << "Please provide a valid .cmnd file as "
      "argument to the -c2 option." << endl;
    return 1;
  }

  // Random number seed.
  string seed = ip.get<string>("s");
  // Output filename.
  string out = ip.get<string>("o");
  // Time event generation.
  bool takeTime = ip.get<bool>("t");
  // Command line number of event, overrides the one set in input .cmnd file.
  int nev = ip.get<int>("n");

  // Catch the splash screen in a buffer.
  stringstream splashBuf;
  std::streambuf* sBuf = cout.rdbuf();
  cout.rdbuf(splashBuf.rdbuf());
  // The Pythia object.
  Pythia pythia;
  // Direct cout back.
  cout.rdbuf(sBuf);

  // UserHooks wrapper
  auto userHooksWrapper = make_shared<UserHooksWrapper>();
  userHooksWrapper->additionalSettings(&pythia.settings);
  pythia.setUserHooksPtr(userHooksWrapper);
  // Some extra parameters.
  pythia.settings.addFlag("Main:writeHepMC",false);
  pythia.settings.addFlag("Main:writeRoot",false);
  pythia.settings.addFlag("Main:runRivet",false);
  pythia.settings.addFlag("Main:rivetIgnoreBeams",false);
  pythia.settings.addMode("Main:rivetDumpPeriod",-1, true, false, -1, 0);
  pythia.settings.addWord("Main:rivetDumpFile", "");
  pythia.settings.addFlag("Main:outputLog",false);
  pythia.settings.addWVec("Main:analyses",vector<string>());
  pythia.settings.addWVec("Main:preload",vector<string>());
  pythia.settings.addWord("Main:rivetRunName","");
  // Read input from external file.
  pythia.readFile(cmndfile);

  if(cmndfile2 != "") pythia.readFile(cmndfile2);

  // Set seed after reading input
  if(seed != "-1") {
    pythia.readString("Random:setSeed = on");
    pythia.readString("Random:seed = "+seed);
  }

  // Read the extra parameters.
  if (nev > -1) pythia.settings.mode("Main:numberOfEvents",nev);
  int nEvent = pythia.mode("Main:numberOfEvents");;
  const bool hepmc = pythia.flag("Main:writeHepMC");
  const bool root = pythia.flag("Main:writeRoot");
  const bool runRivet = pythia.flag("Main:runRivet");
  const bool ignoreBeams = pythia.flag("Main:rivetIgnoreBeams");
  const bool doLog = pythia.flag("Main:outputLog");
  const string rivetrName = pythia.settings.word("Main:rivetRunName");
  const vector<string> rAnalyses = pythia.settings.wvec("Main:analyses");
  const vector<string> rPreload = pythia.settings.wvec("Main:preload");
  const int rivetDump = pythia.settings.mode("Main:rivetDumpPeriod");
  const string rivetDumpName = pythia.settings.word("Main:rivetDumpFile");
  int nError = pythia.mode("Main:timesAllowErrors");
  const bool countErrors = (nError > 0 ? true : false);
  // HepMC conversion object.
  Pythia8ToHepMC ToHepMC;
  if (hepmc)
    ToHepMC.setNewFile((out == "" ? "pythia.hepmc" : out + ".hepmc"));
  // Rivet initialization.
  Pythia8Rivet rivet(pythia,(out == "" ? "Rivet.yoda" : out + ".yoda"));
  rivet.ignoreBeams(ignoreBeams);
  rivet.dump(rivetDump, rivetDumpName);
  for(int i = 0, N = rAnalyses.size(); i < N; ++i){
    string analysis = rAnalyses[i];
    size_t pos = analysis.find(":");
    // Simple case, no analysis parameters.
    if(pos == string::npos)
      rivet.addAnalysis(analysis);
    else {
      string an = analysis.substr(0,pos);
      analysis.erase(0, pos + 1);
      vector<string> pKeys;
      vector<string> pVals;
      while (analysis.find("->") != string::npos) {
        pos = analysis.find(":");
        string par = analysis.substr(0,pos);
        size_t pos2 = par.find("->");
        if (pos2 == string::npos){
           cout << "Error in main144: malformed parameter " << par << endl;
        }
        string pKey = par.substr(0,pos2);
        string pVal = par.substr(pos2+2,par.length());
        pKeys.push_back(pKey);
        pVals.push_back(pVal);
        analysis.erase(0,par.length()+1);
      }
      for (int j = 0, N = pKeys.size(); j < N; ++j)
        an += ":"+pKeys[j]+"="+pVals[j];
      rivet.addAnalysis(an);
    }
  }
  for(int i = 0, N = rPreload.size(); i < N; ++i)
    rivet.addPreload(rPreload[i]);
  rivet.addRunName(rivetrName);
  // Root initialization
  #ifdef PY8ROOT
  TFile* file;
  RootEvent* re;
  TTree* tree;
  #endif
  if (root) {
   // First test if root is available on system.
   #ifndef PY8ROOT
        cout << "Option Main::writeRoot = on requires a working,\n"
                "linked Root installation." << endl;
        return 1;
   #else
   gSystem->Load("main144.so");
   string op = (out == "" ? "pythia.root" : out + ".root");
   file = TFile::Open(op.c_str(),"recreate" );
   re = new RootEvent();
   tree = new TTree("t","Pythia8 event tree");
   tree->Branch("events",&re);
   #endif
  }

  // Logfile initialization.
  ofstream logBuf;
  std::streambuf* oldCout;
  if(doLog) {
    oldCout = cout.rdbuf(logBuf.rdbuf());
    logBuf.open((out == "" ? "pythia.log" : out + ".log"));
  }
  // Option to trash the splash screen.
  ostream cnull(NULL);
  if(ip.get<bool>("l")) cnull << splashBuf.str();
  else cout << splashBuf.str();
  // If Pythia fails to initialize, exit with error.
  if (!pythia.init()) return 1;
  // Make a sanity check of initialized Rivet analyses
  if (!runRivet && rAnalyses.size() > 0 )
    cout << "Warning in main144: Rivet analyses initialized, but runRivet "
         << "set to off." << endl;
  // Loop over events.
  auto startAllEvents = std::chrono::high_resolution_clock::now();
  for ( int iEvent = 0; iEvent < nEvent; ++iEvent ) {
    auto startThisEvent = std::chrono::high_resolution_clock::now();
    if ( !pythia.next() ) {
      if (countErrors && --nError < 0) {
        pythia.stat();
        cout << " \n *-------  PYTHIA STOPPED!  -----------------------*"
             << endl;
        cout << " | Event generation failed due to too many errors. |" << endl;
        cout << " *-------------------------------------------------*" << endl;
        return 1;
      }
      continue;
    }
    auto stopThisEvent = std::chrono::high_resolution_clock::now();
    auto eventTime = std::chrono::duration_cast<std::chrono::milliseconds>
      (stopThisEvent - startThisEvent);
    double tt = eventTime.count();
    if (runRivet) {
      if (takeTime) rivet.addAttribute("EventTime", tt);
      rivet();
    }
    if (hepmc) {
      ToHepMC.writeNextEvent(pythia);
    }

    #ifdef PY8ROOT
    if (root) {
      // If we want to write a root file, the event must be skimmed here.
      vector<RootTrack> rts;
      for(int i = 0; i < pythia.event.size(); ++i) {
        RootTrack t;
        Particle& p = pythia.event[i];
        // Any particle cuts and further track definitions should
        // be implemented in the RootTrack class by the user.
        if (t.init(p)) rts.push_back(t);
      }
      bool fillTree = re->init(&pythia.info);
      re->tracks = rts;
      if(fillTree) tree->Fill();
    }
    #endif
    }
  pythia.stat();
  #ifdef PY8ROOT
  if (root) {
   tree->Print();
   tree->Write();
   delete file;
   delete re;
  }
  #endif
  auto stopAllEvents = std::chrono::high_resolution_clock::now();
  auto durationAll = std::chrono::duration_cast<std::chrono::milliseconds>
    (stopAllEvents - startAllEvents);
  if (takeTime) {
    cout << " \n *-------  Generation time  -----------------------*\n";
    cout << " | Event generation, analysis and writing to files  |\n";
    cout << " | took: " << double(durationAll.count()) << " ms or " <<
      double(durationAll.count())/double(nEvent) << " ms per event     |\n";
    cout << " *-------------------------------------------------*\n";
  }
  // Put cout back in its place.
  if (doLog) cout.rdbuf(oldCout);
  return 0;
}

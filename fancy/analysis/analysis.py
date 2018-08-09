import numpy as np
import pystan
from astropy.coordinates import SkyCoord, EarthLocation, AltAz
from astropy.time import Time
from astropy import units as u
from matplotlib import pyplot as plt

from ..interfaces.integration import ExposureIntegralTable
from ..interfaces.stan import Direction
from ..interfaces import stan_utility
from ..utils import PlotStyle
from ..plotting import AllSkyMap
from ..propagation.energy_loss import get_Eth_src, get_kappa_ex, get_Eex


__all__ = ['Analysis']


class Analysis():
    """
    To manage the running of simulations and fits based on Data and Model objects.
    """

    def __init__(self, data, model, analysis_type = None):
        """
        To manage the running of simulations and fits based on Data and Model objects.
        
        :param data: a Data object
        :param model: a Model object
        :param analysis_type: type of analysis
        """

        self.data = data

        self.model = model

        self.fit_input = None
        self.sim_input = None
        
        self.simulation = None
        self.fit = None

        self.arr_dir_type = 'arrival direction'
        self.joint_type = 'joint'

        if analysis_type == None:
            analysis_type = self.arr_dir_type
        self.analysis_type = analysis_type

        if self.analysis_type == self.joint_type:
            self.Eth_src = get_Eth_src(self.model.Eth, self.data.source.distance)
            self.Eex = get_Eex(self.Eth_src, self.model.alpha)
            self.kappa_ex = get_kappa_ex(self.Eex, self.model.B, self.data.source.distance)
        
        
    def build_tables(self, sim_table_filename, num_points = None, table_filename = None, sim_only = False):
        """
        Build the necessary integral tables.
        """

        self.sim_table_filename = sim_table_filename
        self.table_filename = table_filename 

        params = self.data.detector.params
        varpi = self.data.source.unit_vector

        if self.analysis_type == self.arr_dir_type:
            kappa_true = self.model.kappa
            
        if self.analysis_type == self.joint_type:
            kappa_true = self.kappa_ex


        # kappa_true table for simulation
        self.sim_table = ExposureIntegralTable(kappa_true, varpi, params, self.sim_table_filename)
        self.sim_table.build_for_sim()
        self.sim_table = pystan.read_rdump(self.sim_table_filename)
        
        if not sim_only:
            # logarithmically spcaed array with 60% of points between KAPPA_MIN and 100
            kappa_first = np.logspace(np.log(1), np.log(10), int(num_points * 0.7), base = np.e)
            kappa_second = np.logspace(np.log(10), np.log(100), int(num_points * 0.2) + 1, base = np.e)
            kappa_third = np.logspace(np.log(100), np.log(1000), int(num_points * 0.1) + 1, base = np.e)
            kappa = np.concatenate((kappa_first, kappa_second[1:], kappa_third[1:]), axis = 0)
        
            # full table for fit
            self.table_to_build = ExposureIntegralTable(kappa, varpi, params, self.table_filename)
            self.table_to_build.build_for_fit()
            self.table = pystan.read_rdump(self.table_filename)
        

    def use_tables(self, table_filename, sim_table_filename):
        """
        Pass in names of integral tables that have already been made.
        """
        self.sim_table_filename = sim_table_filename
        self.table_filename = table_filename 
        
        self.sim_table = pystan.read_rdump(self.sim_table_filename)
        self.table = pystan.read_rdump(self.table_filename)
        
        
    def _get_zenith_angle(self, c_icrs, loc, time):
        """
        Calculate the zenith angle of a known point 
        in ICRS (equatorial coords) for a given 
        location and time.
        """
        c_altaz = c_icrs.transform_to(AltAz(obstime = time, location = loc))
        return (np.pi/2 - c_altaz.alt.rad)


    def _simulate_zenith_angles(self):
        """
        Simulate zenith angles for a set of arrival_directions.
        """

        start_time = 2004
        c_icrs = self.arrival_direction.d.icrs 

        time = []
        zenith_angles = []
        stuck = []

        j = 0
        first = True
        for d in c_icrs:
            za = 99
            i = 0
            while (za > self.data.detector.threshold_zenith_angle.rad):
                dt = np.random.exponential(1 / self.Nex_sim)
                if (first):
                    t = start_time + dt
                else:
                    t = time[-1] + dt
                tdy = Time(t, format = 'decimalyear')
                za = self._get_zenith_angle(d, self.data.detector.location, tdy)
                i += 1
                if (i > 100):
                    za = self.data.detector.threshold_zenith_angle.rad
                    stuck.append(1)
            time.append(t)
            first = False
            zenith_angles.append(za)
            j += 1
            #print(j , za)
            
        if (len(stuck) > 1):
            print('Warning: % of zenith angles stuck is', len(stuck)/len(zenith_angles) * 100)

        return zenith_angles

    
    def simulate(self, seed = None):
        """
        Run a simulation.

        :param seed: seed for RNG
        """

        eps = self.sim_table['table'][0]

        # handle selected sources
        if (self.data.source.N < len(eps)):
            eps = [eps[i] for i in self.data.source.selection]
            
        # compile inputs from Model and Data
        self.simulation_input = {
                       'kappa_c' : self.data.detector.kappa_c, 
                       'Ns' : len(self.data.source.distance),
                       'varpi' : self.data.source.unit_vector, 
                       'D' : self.data.source.distance,
                       'A' : self.data.detector.area,
                       'a0' : self.data.detector.location.lat.rad,
                       'theta_m' : self.data.detector.threshold_zenith_angle.rad, 
                       'alpha_T' : self.data.detector.alpha_T,
                       'eps' : eps}

        if self.analysis_type == self.arr_dir_type:

            self.simulation_input['F_T'] = self.model.F_T
            self.simulation_input['f'] = self.model.f
            self.simulation_input['kappa'] = self.model.kappa
            
        if self.analysis_type == self.joint_type:
            
            self.simulation_input['B'] = self.model.B
            
            self.simulation_input['L'] = self.model.L
            self.simulation_input['F0'] = self.model.F0
            
            self.simulation_input['alpha'] = self.model.alpha
            self.simulation_input['Eth'] = self.model.Eth
            self.simulation_input['Eerr'] = self.model.Eerr

            self.simulation_input['Dbg'] = self.model.Dbg
        
        # run simulation
        print('running stan simulation...')
        self.simulation = self.model.simulation.sampling(data = self.simulation_input, iter = 1,
                                                         chains = 1, algorithm = "Fixed_param", seed = seed)

        print('done')

        # extract output
        print('extracting output...')
        self.Nex_sim = self.simulation.extract(['Nex_sim'])['Nex_sim']
        arrival_direction = self.simulation.extract(['arrival_direction'])['arrival_direction'][0]
        self.arrival_direction = Direction(arrival_direction)

        if self.analysis_type == self.joint_type:
            
            self.Edet = self.simulation.extract(['Edet'])['Edet'][0]
            self.Earr = self.simulation.extract(['Earr'])['Earr'][0]
            self.E = self.simulation.extract(['E'])['E'][0]
        
        print('done')

        # simulate the zenith angles
        print('simulating zenith angles...')
        self.zenith_angles = self._simulate_zenith_angles()
        print('done')
        
        eps_fit = self.table['table']
        kappa_grid = self.table['kappa']

        # handle selected sources
        if (self.data.source.N < len(eps_fit)):
            eps_fit = [eps_fit[i] for i in self.data.source.selection]
        
        # prepare fit inputs
        print('preparing fit inputs...')
        self.fit_input = {'Ns' : len(self.data.source.distance), 
                          'varpi' :self.data.source.unit_vector,
                          'D' : self.data.source.distance, 
                          'N' : len(self.arrival_direction.unit_vector), 
                          'arrival_direction' : self.arrival_direction.unit_vector, 
                          'A' : np.tile(self.data.detector.area, len(self.arrival_direction.unit_vector)),
                          'kappa_c' : self.data.detector.kappa_c,
                          'alpha_T' : self.data.detector.alpha_T, 
                          'Ngrid' : len(kappa_grid), 
                          'eps' : eps_fit, 
                          'kappa_grid' : kappa_grid,
                          'zenith_angle' : self.zenith_angles}

        if self.analysis_type == self.joint_type:
            
            self.fit_input['Edet'] = self.Edet
            self.fit_input['Eth'] = self.model.Eth
            self.fit_input['Eerr'] = self.model.Eerr
            self.fit_input['Dbg'] = self.model.Dbg
            
        print('done')
        
        
    def save_simulated_data(self, filename):
        """
        Write the simulated data to file.
        """
        if self.fit_input != None:
            pystan.stan_rdump(self.fit_input, filename)
        else:
            print("Error: nothing to save!")

            
    def plot_simulation(self, cmap = None):
        """
        Plot the simulated data on a skymap
        """
        
        # plot style
        if cmap == None:
            style = PlotStyle()
        else:
            style = PlotStyle(cmap_name = cmap)
            
        # figure
        fig = plt.figure(figsize = (12, 6));
        ax = plt.gca()

        # skymap
        skymap = AllSkyMap(projection = 'hammer', lon_0 = 0, lat_0 = 0);

        self.data.source.plot(style, skymap)
        self.data.detector.draw_exposure_lim(skymap)
       
        labels = (self.simulation.extract(['lambda'])['lambda'][0] - 1).astype(int)

        cmap = plt.cm.get_cmap('plasma', 17) 
        label = True
        for lon, lat, lab in np.nditer([self.arrival_direction.lons, self.arrival_direction.lats, labels]):
            if (lab == 17):
                color = 'k'
            else:
                color = cmap(lab)
            if label:
                skymap.tissot(lon, lat, 5, npts = 30, facecolor = color,
                              alpha = 0.5, label = 'simulated data')
                label = False
            else:
                skymap.tissot(lon, lat, 5, npts = 30, facecolor = color, alpha = 0.5)

        # standard labels and background
        skymap.draw_standard_labels(style.cmap, style.textcolor)

        # legend
        plt.legend(bbox_to_anchor = (0.85, 0.85))
        leg = ax.get_legend()
        frame = leg.get_frame()
        frame.set_linewidth(0)
        frame.set_facecolor('None')
        for text in leg.get_texts():
            plt.setp(text, color = style.textcolor)

        
    def use_simulated_data(self, filename):
        """
        Read in simulated data from a file.
        """

        self.fit_input = pystan.read_rdump(filename)

        
    def use_uhecr_data(self):
        """
        Build fit inputs from the UHECR dataset.
        """

        eps_fit = self.table['table']
        kappa_grid = self.table['kappa']

        # handle selected sources
        if (self.data.source.N < len(eps_fit)):
            eps_fit = [eps_fit[i] for i in self.data.source.selection]
        
        print('preparing fit inputs...')
        self.fit_input = {'Ns' : len(self.data.source.distance),
                          'varpi' :self.data.source.unit_vector,
                          'D' : self.data.source.distance,
                          'N' : len(self.data.uhecr.energy),
                          'arrival_direction' : self.data.uhecr.unit_vector,
                          'A' : self.data.uhecr.A,
                          'kappa_c' : self.data.detector.kappa_c,
                          'alpha_T' : self.data.detector.alpha_T,
                          'Ngrid' : len(kappa_grid),
                          'eps' : eps_fit,
                          'kappa_grid' : kappa_grid,
                          'zenith_angle' : self.data.uhecr.incidence_angle}

        if self.analysis_type == self.joint_type:

            self.fit_input['Edet'] = self.Edet
            self.fit_input['Eth'] = self.model.Eth
            self.fit_input['Eerr'] = self.model.Eerr
            self.fit_input['Dbg'] = self.model.Dbg
            
        print('done')

        
    def fit_model(self, iterations = 1000, chains = 4, seed = None):
        """
        Fit a model.

        :param iterations: number of iterations
        :param chains: number of chains
        :param seed: seed for RNG
        """

        # fit
        self.fit = self.model.model.sampling(data = self.fit_input, iter = iterations, chains = chains, seed = seed)

        # Diagnositics
        stan_utility.check_treedepth(self.fit)
        stan_utility.check_div(self.fit)
        stan_utility.check_energy(self.fit)

        return self.fit

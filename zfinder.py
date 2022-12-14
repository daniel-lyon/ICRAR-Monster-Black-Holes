# Import libraries
import numpy as np
import matplotlib.pyplot as plt

# Import functions
from time import time
from random import random
from decimal import Decimal
from astropy.wcs import WCS
from astropy.io import fits
from PyAstronomy import pyasl
from sslf.sslf import Spectrum
from warnings import filterwarnings
from scipy.optimize import curve_fit
from scipy.spatial.distance import cdist
from scipy.stats import binned_statistic
from photutils.background import Background2D
from photutils.aperture import CircularAperture, CircularAnnulus, ApertureStats, aperture_photometry

def average_zeroes(data: list):
    ''' Take the average of adjacent points (left and right) if the value is zero'''

    for i, val in enumerate(data):
        if val == 0:
            data[i] = (data[i-1] + data[i+1])/2
    return data

def count_decimals(number: float):
    ''' count the amount of numbers after the decimal place '''

    d = Decimal(str(number))
    d = abs(d.as_tuple().exponent)
    return d

def flatten_list(input_list: list):
    ''' Turns lists of lists into a single list '''

    flattened_list = []
    for array in input_list:
        for x in array:
            flattened_list.append(x)
    return flattened_list

def get_eng_exponent(number: float):
    ''' Get the exponent of a number in engineering format. In eng
        format, exponents are multiples of 3. E+0, E+3, E+6, etc.
        Also returns the unit prefix symbol for the exponent from
        -24 to +24 '''

    # A dictionary of exponent and unit prefix pairs
    prefix = {-24 : 'y', -21 : 'z', -18 : 'a',-15 : 'f', -12 : 'p',
        -9 : 'n', -6 : 'mu', -3 : 'm', 0 : '', 3 : 'k', 6 : 'M',
        9 : 'G', 12 : 'T', 15 : 'P', 18 : 'E', 21 : 'Z', 24 : 'Y'}

    base = np.log10(np.abs(number)) # Log rules to find exponent
    exponent = int(np.floor(base)) # convert to floor integer
    
    # Check if the the exponent is a multiple of 3
    for i in range(3):
        if (exponent-i) % 3 == 0:
            
            # Return the exponent and associated unit prefix
            symbol = prefix[exponent-i]
            return exponent-i, symbol

class RedshiftFinder(object):
    def __init__(self, image: str, right_ascension: list, declination: list, aperture_radius: float,
            bvalue: float, num_plots=1, minimum_point_distance=1.0, warnings=False):

        '''
        `RedshiftFinder` looks at transition lines and attempts to find the best fitting red shift.
        This operates by plotting gaussian functions over the data and calculating the chi-squared
        at small changes in red shift. By taking the minimised chi-squared result, the most likely 
        red shift result is returned. Unrealistic and/or bad fits penalise the chi2 to be higher.

        Parameters
        ----------
        image : `str`
            An image of the `.fits` file type. Must be a three demensional image with axes Ra, Dec, 
            & Freq. 
        
        right_ascension : `list`
            The right ascension of the target object. Input as [h, m, s]
        
        declination : `list`
            The declination of the target object. input as [d, m, s, esign]. Esign is -1 or 1
            depending on if the decination is positive or negative.
        
        aperture_radius : `float`
            The radius of the aperture which the image used in pixels. This is converted to
            degrees when calculating the flux

        bvalue : 'float`
            The value of the BMAJ and BMIN vaues

        num_plots : `int`, optional
            The number  of random points to work with and plot. Default = 1
        
        minimum_point_distance : `float`, optional
            The distance between random points in pixels. Default = 1.0

        warnings : `bool`, optional
            Optional setting to display warnings or not. If True, warnings are displayed.
            Default = False
        '''

        # Main data
        self.image = fits.open(image)
        self.hdr = self.image[0].header
        self.data = self.image[0].data[0]
        self.ra = right_ascension
        self.dec = declination
        self.aperture_radius = aperture_radius
        self.minimum_point_distance = minimum_point_distance
        self.num_plots = num_plots
        self.circle_radius = self.fits_circle_radius(self.data[-1])

        # Initialise Lists
        self.all_chi2 = []
        self.all_flux = []
        self.all_params = []
        self.all_snrs = []
        self.all_scales = []
        self.all_lowest_z = []
        self.plot_colours = []

        # The area of the beam
        bmaj = bvalue/3600
        bmin = bmaj
        self.barea = 1.1331 * bmaj * bmin

        # Conversion of pixels to degrees for calculating flux
        self.pix2deg = self.hdr['CDELT2'] # unit conversion 

        # There are many, many warnings
        if not warnings:
            filterwarnings("ignore", module='photutils.background')
            filterwarnings("ignore", module='astropy.wcs.wcs')
            filterwarnings("ignore", module='scipy.optimize')
    
    @staticmethod
    def fits_circle_radius(data: np.ndarray[np.ndarray]):
        ''' In a fits image, find the radius of the smallest image. This radius is used in
        The `spaced_circle_points` function as the radius.

        Parameters
        ----------
        data : `list`
            A list of lists corresponding to the smallest sized image in the frequency
            of a .fits image. AKA: the image with the most nans (usually the last image)
        
        Returns
        -------
        largest_radius : `int`
            The radius of the given image
        '''

        # Assuming the image is a cube with a circle of non-nan values
        data_len = len(data[0]) # The total length
        target_row = data[(data_len//2) - 1] # the middle row
        
        # The true radius is the total length minus the number of nans
        nan_count = sum(np.isnan(x) for x in target_row) 
        diameter = data_len - nan_count
        radius = (diameter // 2) - 7 # minus 7 as a little buffer

        return radius

    @staticmethod
    def wcs2pix(ra: list, dec: list, hdr):

        # TODO: rounded x and y values are different to non rounded versions?

        ''' Convert right ascension and declination to x, y positional world coordinates

        Parameters
        ----------
        ra : `list`
            Right ascension coordinate given [h, m, s]
        
        dec : `list`
            Declination coordinate given as [d, m, s, esign]

        hdr : `astropy.io.fits.header.Header`
            The image header
        
        Returns
        -------
        x : `int`
            The transformed ra to world coordinate
        
        y : `int`
            The transformed dec to world coordinate
        '''

        w = WCS(hdr) # Get the world coordinate system
    
        # If there are more than 2 axis, drop them
        if hdr['NAXIS'] > 2:
            w = w.dropaxis(3) # stokes
            w = w.dropaxis(2) # frequency

        # Convert to decimal degrees
        ra = pyasl.hmsToDeg(ra[0], ra[1], ra[2])
        dec = pyasl.dmsToDeg(dec[0], dec[1], dec[2], esign=dec[3])

        # Convert world coordinates to pixel
        x, y = w.all_world2pix(ra, dec, 1)

        # Round to nearest integer
        x = int(np.round(x))
        y = int(np.round(y))

        return x, y
    
    @staticmethod
    def spaced_circle_points(num_points: int, circle_radius: float, centre_coords: list[float], minimum_spread_distance: float):
        ''' Generate points in a circle that are a minimum distance a part

        Parameters
        ----------
        num_points : `int`
            The number of points to plot. Defaults to 1
        
        circle_radius : `float`
            The radius in which points can be plotted around the centre. Defaults to 50
        
        centre_coords : `list`
            The centre of the circle.
        
        minimum_spread_distance : `float`
            The minimum distance between points.
        
        Returns
        -------
        points: `list`
            A list of points containing x, y coordinates.
        '''

        # centre_coords = [x,y] -> points = [[x,y]]
        points = [centre_coords]
        
        # Iterate through the number of points.
        for _ in range(num_points-1):

            # Keep generating the current point until it is at least the minimum distance away from all 
            while True:
                theta = 2 * np.pi * random() # choose a random direction
                r = circle_radius * random() # choose a random radius

                # Convert coordinates to cartesian
                x = r * np.cos(theta) + centre_coords[0]
                y = r * np.sin(theta) + centre_coords[1]

                # Find the distance between all the placed points
                distances = cdist([[x,y]], points, 'euclidean')
                min_distance = min(distances[0])
                
                # If the minimum distance is satisfied for all points, go to next point
                if min_distance >= minimum_spread_distance or len(points) == 1:
                    points.append([x,y])
                    break

        return points
    
    @staticmethod
    def gaussf(x, a, s, x0, n):
        
        # TODO: add unfixed variable for y0?

        ''' Gaussian function used to fit to a data set

        Parameters
        ----------
        x : `list`
            The x-axis list
        
        a : `float`
            The amplitude of the gaussians
        
        s : `float`
            The standard deviation and width of the gaussians
        
        x0 : `float`
            The x-axis offset

        n : `int`
            The number of gaussian functions
        
        Returns
        -------
        y : `list`
            A y-axis list of guassians 
        '''

        y = 0
        for i in range(1,n):
            y += (a * np.exp(-((x-i*x0) / s)**2)) # i = 1,2,3 ... 9, 10
        return y
    
    def fits_flux(self, position):
        ''' For every frequency channel, find the flux and associated uncertainty.

        Parameters
        ----------
        position : `list`
            an x,y coodinate to measure the flux at.

        Returns
        -------
        fluxes : 'numpy.ndarray'
            A numpy array of fluxes at every frequency
        
        uncertainties : 'numpy.ndarray'
            A numpy array of uncertainties at every frequency
        '''

        # Initialise array of fluxes and uncertainties to be returned
        fluxes = np.array([])
        uncertainties = np.array([])
        
        # For every page of the 3D data matrix, find the flux around a point (aperture)
        for page in self.data:

            # Setup the apertures 
            aperture = CircularAperture(position, self.aperture_radius)
            annulus = CircularAnnulus(position, r_in=2*self.aperture_radius, r_out=3*self.aperture_radius)

            # Uncertainty
            aperstats = ApertureStats(page, annulus) 
            rms  = aperstats.std 

            # Background
            bkg = Background2D(page, (50, 50)).background 

            # Aperture sum of the fits image minus the background
            apphot = aperture_photometry(page - bkg, aperture)
            apsum = apphot['aperture_sum'][0]

            # Calculate corrected flux
            total_flux = apsum*(self.pix2deg**2)/self.barea
            fluxes = np.append(fluxes, total_flux)
            uncertainties = np.append(uncertainties, rms)

        return fluxes, uncertainties      

    def zfind(self, ftransition, z_start=0, dz=0.01, z_end=10):
        ''' For every point in coordinates, find the flux and uncertainty. Then find the significant
        lines with the line finder. For each point, iterate through all redshift values and calculate
        the chi-squared that corresponds to that redshift by fitting gaussians to overlay the flux. If
        The points found by the line finder do not match within 4 frequency channels of the gaussian
        peaks, penalise the chi-squared at that redshift by a factor of 1.2.

        Parameters
        ----------
        ftransition : `float`
            The first transition frequency (in GHz) of the target element/molecule/etc
        
        z_start : `float`, optional
            The starting value of redshift value. Default = 0
        
        dz : `float`, optional
            The change in redshift to iterature through. Default = 0.01
        
        z_end : `float`, optional
            The final redshift  value. Default = 10
        
        Returns
        -------
        self.all_lowest_z : list
            A list of the lowest measured redshift values with length equal to the number of points.
        '''
        
        # Object values
        self.dz = dz
        self.ftransition = ftransition 

        # Setup for spaced random points
        self.centre_x, self.centre_y = self.wcs2pix(self.ra, self.dec, self.hdr) 

        # Generate the random coordinates for statistical analysis
        self.coordinates = self.spaced_circle_points(self.num_plots, self.circle_radius, 
            centre_coords=[self.centre_x, self.centre_y], minimum_spread_distance=self.minimum_point_distance)
        
        # Convert the x-axis to GHz
        exponent, self.symbol = get_eng_exponent(self.hdr['CRVAL3'])
        freq_start = self.hdr['CRVAL3']/10**exponent # {symbol}Hz
        freq_incr = self.hdr['CDELT3']/10**exponent # {symbol}Hz
        freq_len = np.shape(self.data)[0] # length
        freq_end = freq_start + freq_len * freq_incr # where to stop
        self.x_axis_flux = np.linspace(freq_start, freq_end, freq_len) # axis to plot

        # Create the redshift values to iterate through
        self.z = np.arange(z_start, z_end+dz, dz)
        self.num_gaussians = round(z_end - z_start) # the number of guassians to plot

        start = time() # Measure how long it takes to execute 

        # For every coodinate point, find the associated flux and uncertainty 
        for index, coord in enumerate(self.coordinates):

            # Initialise arrays for each coordinate
            chi2_array = [] 
            param_array = []

            # Get fluxes and uncertainties at each point
            y_flux, uncert = self.fits_flux(coord)
            uncert = average_zeroes(uncert) # average 0's from values left & right
            y_flux *= 1000; uncert *= 1000 # convert from uJy to mJy
            
            # Create a line finder to find significant points
            s = Spectrum(y_flux)
            s.find_cwt_peaks(scales=np.arange(4,10), snr=3)
            spec_peaks = s.channel_peaks
            spec_peaks = np.sort(spec_peaks) # sort the peaks from left to right instead of right to left
            num_spec_peaks = len(spec_peaks)

            # Calculate the ratio of the snrs and scales
            snrs = [round(i,2) for i in s.peak_snrs] # the snrs of the peaks
            scales = [i[1]-i[0] for i in s.channel_edges] # the scales of the peaks

            # For every redshift, calculate the corresponding chi squared value
            for ddz in self.z:
                loc = ftransition/(1+ddz) # location of the gaussian peaks
                
                # Determine the best fitting parameters
                try:
                    params, covariance = curve_fit(lambda x, a, s: self.gaussf(x, a, s, x0=loc, n=self.num_gaussians), 
                        self.x_axis_flux, y_flux, bounds=[[0, (1/8)], [max(y_flux), (2/3)]], absolute_sigma=True) # best fit
                except RuntimeError:
                    chi2_array.append(max(chi2_array)) # if no returned parameters, set the chi-squared for this redshift to the maximum
                    continue
                
                # Using the best fit parameters, calculate the chi2 corresponding to this redshift {ddz}
                f_exp = self.gaussf(self.x_axis_flux, a=params[0], s=params[1], x0=loc, n=self.num_gaussians) # expected function
                chi2 = sum(((y_flux - f_exp) / uncert)**2)

                # Find the location of the expected gaussian peaks
                if num_spec_peaks != 0:
                    exp_peak = np.argsort(f_exp)[-num_spec_peaks:] # the index of the gaussian peaks
                    exp_peak = np.sort(exp_peak) # sort ascending
                else:
                    exp_peak = []

                # Calculate the peak_distance beween the spectrum and expected peaks
                delta_peaks = []
                for p1, p2 in zip(spec_peaks, exp_peak):
                    delta_peaks.append(abs(p1-p2))
                peak_distance = sum(delta_peaks)

                # If the peak_distance is greater than the number of spectrum peaks multiplied by 3 channels,
                # or if there are no peaks, penalise the chi2 by multiplying it my 1.2
                if peak_distance > num_spec_peaks*3 or num_spec_peaks < 2:
                    chi2 *= 1.2

                # Append parameters for use later
                chi2_array.append(chi2)
                param_array.append(params)

            # Find the colours to map to each chi2
            min_plot_chi2 = min(chi2_array)
            if index == 0:
                self.plot_colours.append('black') # the original
                target_chi2 = min_plot_chi2
            elif min_plot_chi2 <= target_chi2:
                self.plot_colours.append('red') # if chi2 lower than original
            elif min_plot_chi2 > target_chi2 and min_plot_chi2 <= 1.05*target_chi2:
                self.plot_colours.append('gold') # if chi2 within 5% above the original
            else:
                self.plot_colours.append('green') # if chi2 more than 5% above the original

            # Find the lowest redshift of each source point
            lowest_index = np.argmin(chi2_array)
            lowest_redshift = self.z[lowest_index]
            
            # Append parameters for use later
            self.all_flux.append(y_flux)
            self.all_chi2.append(chi2_array)
            self.all_params.append(param_array)
            self.all_snrs.append(snrs)
            self.all_scales.append(scales)
            self.all_lowest_z.append(lowest_redshift)

            print(f'{index+1}/{len(self.coordinates)} completed..')

        # Return an array with the lowest redshift from each source
        end = time()
        print(f'Data processed in {round((end-start)/60, 3)} minutes')
        return self.all_lowest_z

class RedshiftPlotter(RedshiftFinder):
    def __init__(self, obj, plots_per_page=25):

        # TODO: Add number of rows (or maybe columns instead?) to automatically calculate plots per page
        # TODO: Change saving to work for multiple pages

        ''' `RedshiftPlotter` takes a `RedshiftFinder` object as an input to easily compute plots
        used for statistical analysis.
        '''

        # Values
        self.obj = obj

        # The number of pages of data to plot
        self.pages = self.obj.num_plots // plots_per_page
        if self.pages == 0:
            self.pages = 1
        
        # The number of rows and columns used for each page
        if self.obj.num_plots >= 5:
            self.cols = 5
            self.rows = self.obj.num_plots // (self.cols * self.pages)
            self.squeeze = True
        else:
            self.cols = 1
            self.rows = 1
            self.squeeze = False
        
    @staticmethod
    def plot_peaks(y_axis, x_axis, plot_type):

        # TODO: Find a way to remove this function? (Combine with other Spectrum function?)
        # TODO: Fix text plotting of snrs and scales
        # TODO: Animations for flux's and chi-squared's

        ''' Plot the found peaks of a line finder on top of another plot.

        Parameters
        ----------
        y_axis : `list`
            The y-axis with which to find the significant peaks

        x_axis : `list`
            The x-axis with which to plot the significant peaks

        plot_type : 'matplotlib.axes._subplots.AxesSubplot'
            : The figure to plot the peaks on 
        '''

        s = Spectrum(y_axis)
        s.find_cwt_peaks(scales=np.arange(4,10), snr=3)
        peaks = s.channel_peaks

        scales = [i[1]-i[0] for i in s.channel_edges]
        snrs = [round(i,2) for i in s.peak_snrs]
        snr_text = [0.35 if i%2==0 else -0.35 for i in range(len(s.peak_snrs))]
        scale_text = [0.40 if i%2==0 else -0.40 for i in range(len(scales))]

        for i, snr, s_text, sc_text, sc in zip(peaks, snrs, snr_text, scale_text, scales):
            plot_type.plot(x_axis[i], y_axis[i], marker='o', color='blue')
            plot_type.text(x_axis[i], s_text, s=f'snr={snr}', color='blue')
            plot_type.text(x_axis[i], sc_text, s=f'scale={sc}', color='blue')

    def plot_points(self, savefile=None):
        ''' Plot the distribution of coordinates

        Parameters
        ----------
        savefile : `str`, None, optional
            The filename of the saved figure. Default = None
        '''

        circle_points = np.transpose(self.obj.coordinates)
        points_x = circle_points[0, :] # all x coordinates except the first which is the original
        points_y = circle_points[1, :] # all y coordinates except the first which is the original
        circ = plt.Circle((self.obj.centre_x, self.obj.centre_y), self.obj.circle_radius, fill=False, color='blue')
        fig, ax = plt.subplots()
        fig.set_figwidth(7)
        fig.set_figheight(7)
        ax.add_patch(circ)
        plt.title('Distribution of spaced random points')
        plt.scatter(points_x, points_y, color=self.obj.plot_colours)
        plt.xlim(-self.obj.circle_radius-1+self.obj.centre_x, self.obj.circle_radius+1+self.obj.centre_x)
        plt.ylim(-self.obj.circle_radius-1+self.obj.centre_y, self.obj.circle_radius+1+self.obj.centre_y)
        plt.xlabel('x')
        plt.ylabel('y')
        if savefile != None:
            plt.savefig(f'{savefile}', dpi=200)
        plt.show()
    
    def plot_chi2(self, savefile=None):
        ''' Plot the chi-squared vs redshift at every coordinate

        Parameters
        ----------
        savefile : `str`, None, optional
            The filename of the saved figure. Default = None
        '''

        all_chi2 = np.array_split(self.obj.all_chi2, self.pages)
        AllColours = np.array_split(self.obj.plot_colours, self.pages)
        AllCoords = np.array_split(self.obj.coordinates, self.pages)
        
        # Plot the reduced chi-squared histogram(s) across multiple pages (if more than one)
        for chi2, colours, coordinates in zip(all_chi2, AllColours, AllCoords):

            # Setup the figure and axes
            fig, axs = plt.subplots(self.rows, self.cols, tight_layout=True, sharex=True, squeeze=self.squeeze)
            fig.supxlabel('Redshift')
            fig.supylabel('$\chi^2$', x=0.01)
            axs = axs.flatten()

            # Plot the chi-squared(s) and redshift
            for index, (c2, color, coordinate) in enumerate(zip(chi2, colours, coordinates)):
                lowest_redshift = self.obj.z[np.argmin(c2)]
                axs[index].plot(self.obj.z, c2, color=color)
                axs[index].plot(lowest_redshift, min(c2), 'bo', markersize=5)
                coord = np.round(coordinate, 2)
                axs[index].set_title(f'x,y = {coord}. Min Chi2 = {round(min(c2), 2)}')
                axs[index].set_yscale('log')

            # Save the file and show
            if savefile != None:
                fig.savefig(f'{savefile}', dpi=200)
            plt.show()

    def plot_flux(self, savefile=None):

        # TODO: Change outside boarder colour to use all_colours

        ''' Plot the flux vs frequency at every coordinate

        Parameters
        ----------
        savefile : `str`, None, optional
            The filename of the saved figure. Default = None
        '''

        # Split data into pages
        all_chi2 = np.array_split(self.obj.all_chi2, self.pages)
        all_flux = np.array_split(self.obj.all_flux, self.pages)
        all_params = np.array_split(self.obj.all_params, self.pages)
        d = count_decimals(self.obj.dz) # decimal places to round to

        # Plot the reduced chi-squared histogram(s) across multiple pages (if more than one)
        for fluxes, chi2, params in zip(all_flux, all_chi2, all_params):

            # Setup the figure and axes
            fig, axs = plt.subplots(self.rows, self.cols, tight_layout=True, 
                sharex=True, sharey=True, squeeze=self.squeeze)
            fig.supxlabel(f'Frequency $({self.obj.symbol}Hz)$')
            fig.supylabel('Flux $(mJy)$')
            axs = axs.flatten()

            # Plot the flux(s) and best fit gaussians
            for index, (flux, c2, param) in enumerate(zip(fluxes, chi2, params)):
                lowest_index = np.argmin(c2)
                lowest_redshift = self.obj.z[lowest_index]
                axs[index].plot(self.obj.x_axis_flux, flux, color='black', drawstyle='steps-mid')
                axs[index].plot(self.obj.x_axis_flux, self.obj.gaussf(self.obj.x_axis_flux, *param[lowest_index], 
                    x0=self.obj.ftransition/(1+lowest_redshift), n=self.obj.num_gaussians), color='red')
                axs[index].margins(x=0)
                axs[index].fill_between(self.obj.x_axis_flux, flux, 0, where=(flux > 0), color='gold', alpha=0.75)
                axs[index].set_title(f'z={round(lowest_redshift, d)}')
                self.plot_peaks(flux, self.obj.x_axis_flux, axs[index])
            
            # Save the file and show
            if savefile != None:
                fig.savefig(f'{savefile}', dpi=200)
            plt.show()
    
    def plot_hist_chi2(self, savefile=None):
        ''' Plot a histogram of the chi-squared at every coordinate

        Parameters
        ----------
        savefile : `str`, None, optional
            The filename of the saved figure. Default = None
        '''

        # Initialise return arrays
        all_std = []
        all_mean = []

        # Split data into pages
        all_chi2 = np.array_split(self.obj.all_chi2, self.pages)
        colours = np.array_split(self.obj.plot_colours, self.pages)
        
        # Plot the reduced chi-squared histogram(s) across multiple pages (if more than one)
        for page, color in zip(all_chi2, colours):

            # Setup the figure and axes
            fig, axs = plt.subplots(self.rows, self.cols, tight_layout=True, sharey=True, squeeze=self.squeeze)
            fig.supxlabel('$\chi^2$') 
            fig.supylabel('Count (#)')
            axs = axs.flatten()

            for index, (chi2, c) in enumerate(zip(page, color)):
                reduced_chi2 = np.array(chi2)/(len(chi2)-1)
                axs[index].hist(reduced_chi2, 20, color=c)
                axs[index].set_yscale('log')

                # Calculate the mean and standard deviation of each bin
                mean = binned_statistic(reduced_chi2, reduced_chi2, 'mean', 20)
                std = binned_statistic(reduced_chi2, reduced_chi2, 'std', 20)

                # Flip the arrays to be read left to right and append to the return arrays
                all_mean.append(np.flip(mean[0]))
                all_std.append(np.flip(std[0]))

            # Save the file and show
            if savefile != None:
                fig.savefig(f'{savefile}', dpi=200)
            plt.show()

        return all_mean, all_std

    def plot_snr_scales(self, savefile=None):
        ''' Plot a hsitogram of the snr and scale

        Parameters
        ----------
        savefile : `str`, None, optional
            The filename of the saved figure. Default = None
        '''

        snrs = flatten_list(self.obj.all_snrs)
        scales = flatten_list(self.obj.all_scales)

        # Setup the figure and axes
        fig, (ax_snr, ax_scale) = plt.subplots(1, 2, sharey=True)
        fig.supylabel('Count (#)')

        # Plot the snrs histogram(s)
        ax_snr.hist(snrs, 20)
        ax_snr.set_title('SNR histogram')
        ax_snr.set_xlabel('SNR')

        # Plot the scales histogram
        ax_scale.hist(scales, [8,10,12,14,16,18,20])
        ax_scale.set_title('Scales Histogram')
        ax_scale.set_xlabel('Scale')

        # Save the file and show
        if savefile != None:
            fig.savefig(f'{savefile}', dpi=200)
        plt.show()

if __name__ == '__main__':
    
    image = '0856_cube_c0.4_nat_80MHz_taper3.fits'
    ra = [8, 56, 14.8] # Right Ascenion (h, m, s)
    dec = [2, 24, 0.6, 1] # Declination (d, m, s, sign)
    aperture_radius = 3 # Aperture Radius (pixels)
    bvalue = 3 # BMAJ & BMIN (arcseconds)
    num_plots = 1 # Number of plots to make (must be a multiple of 5 or 1)
    min_sep = 1 # Minimum separation between points (pixels)
    ftransition = 115.2712 # the first transition in GHz
    z_start = 0 # initial redshift
    dz = 0.01 # change in redshift
    z_end = 10 # final redshift

    zfind1 = RedshiftFinder(image, ra, dec, aperture_radius, bvalue, num_plots, min_sep)
    zfind1.zfind(ftransition, z_start, dz, z_end)

    zf1 = RedshiftPlotter(zfind1)
    zf1.plot_points()
    zf1.plot_flux()
    zf1.plot_chi2()
    zf1.plot_hist_chi2()
    zf1.plot_snr_scales()
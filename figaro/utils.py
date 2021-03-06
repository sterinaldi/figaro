import numpy as np

import warnings
from pathlib import Path

from distutils.spawn import find_executable

import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib import axes
from matplotlib.projections import projection_registry
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from corner import corner

from tqdm import tqdm

from collections import Counter
import scipy.stats

from figaro.transform import transform_to_probit
from figaro.marginal import marginalise
from figaro.credible_regions import ConfidenceArea

if find_executable('latex'):
    rcParams["text.usetex"] = True
rcParams["xtick.labelsize"]=14
rcParams["ytick.labelsize"]=14
rcParams["xtick.direction"]="in"
rcParams["ytick.direction"]="in"
rcParams["legend.fontsize"]=12
rcParams["axes.labelsize"]=16
rcParams["axes.grid"] = True
rcParams["grid.alpha"] = 0.6
rcParams["contour.negative_linestyle"] = 'solid'

#-–––––––––-#
# Utilities #
#-----------#

def recursive_grid(bounds, n_pts):
    """
    Recursively generates the n-dimensional grid points (extremes are excluded).
    
    Arguments:
        :list-of-lists bounds: extremes for each dimension (excluded)
        :int n_pts:            number of points for each dimension
        
    Returns:
        :np.ndarray: grid
    """
    bounds = np.atleast_2d(bounds)
    n_pts  = np.atleast_1d(n_pts)
    if len(bounds) == 1:
        d  = np.linspace(bounds[0,0], bounds[0,1], n_pts[0]+2)[1:-1]
        dD = d[1]-d[0]
        return np.atleast_2d(d).T, [dD]
    else:
        grid_nm1, diff = recursive_grid(np.array(bounds)[1:], n_pts[1:])
        
        d = np.linspace(bounds[0,0], bounds[0,1], n_pts[0]+2)[1:-1]
        diff.append(d[1]-d[0])
        grid     = []
        for di in d:
            for gi in grid_nm1:
                grid.append([di,*gi])
        return np.array(grid), diff

def rejection_sampler(n_draws, f, bounds, selfunc = None):
    """
    1D rejection sampler, allows for a selection function
    
    Arguments:
        :int n_draws:      number of draws
        :callable f:       probability density to sample from
        :iterable bounds:  upper and lower bound
        :callable selfunc: selection function, must support numpy arrays
    
    Returns:
        :np.ndarray: samples
    """
    n_draws = int(n_draws)
    if selfunc is None:
        selfunc = lambda x: 1
    x   = np.linspace(bounds[0], bounds[1], 1000)
    top = np.max(f(x)*selfunc(x))
    samples = []
    while len(samples) < n_draws:
        pts   = np.random.uniform(bounds[0], bounds[1], size = n_draws)
        probs = f(pts)*selfunc(pts)
        h     = np.random.uniform(0, top, size = n_draws)
        samples.extend(pts[np.where(h < probs)])
    return np.array(samples).flatten()[:n_draws]

def get_priors(bounds, samples = None, mean = None, std = None, cov = None, df = None, k = None):
    """
    This method takes the prior parameters for the Normal-Inverse-Wishart distribution in the natural space and returns them as parameters in the probit space, ordered as required by FIGARO. In the following, D will denote the dimensionality of the inferred distribution.

    Four parameters are returned:
    * df, is the number of degrees of freedom for the Inverse Wishart distribution,. It must be greater than D+1. If this parameter is None or does not satisfy the condition df > D+1, the default value D+2 is used;
    * k is the scale parameter for the multivariate Normal distribution. Suggested values are  k <~ 1e-1. If None, the default value 1e-2 is used.
    * mu is the mean of the multivariate Normal distribution. It can be either estimated from the available samples or passed directly as a 1D array with length D (the keyword argument mean overrides the samples). If None, the default value 0 (corresponding to the parameter space center) is used.
    * L is the expected value for the Inverse Wishart distribution. This parameter can be either (in descending priority order):
        * passed as 2D array with shape (D,D), the covariance matrix - keyword cov;
        * passed as 1D array with shape (D,) or double: vector of standard deviations (if double, it assumes that the same std has to be used for all dimensions) - keyword std;
        * estimated from samples - keyword samples.
       
    The order in which they are returned is (k,L,df,mu).
    
    Arguments:
        :np.ndarray bounds:         boundaries for probit transformation
        :np.ndarray samples:        2D array with samples
        :double or np.ndarray mean: mean
        :double or np.ndarray std:  expected standard deviation (if double, the same expected std is used for all dimensions, if np.ndarray must match the number of dimensions)
        :np.ndarray cov:            covariance matrix
        :int df:                    degrees of freedom for Inverse Wishart distribution
        :double k:                  scale parameter for Normal distribution
        
    Returns:
        :tuple: prior parameters ordered as in H/DPGMM
    """
    bounds = np.atleast_2d(bounds)
    dim = len(bounds)
    if samples is not None:
        if len(np.shape(samples)) < 2:
            samples = np.atleast_2d(samples).T
        probit_samples = transform_to_probit(samples, bounds)
    # DF
    if df is not None and df > dim+2:
        df_out = df
    else:
        df_out = dim+2
        
    draw_flag = False
    # Mu
    if mean is not None:
        mean = np.atleast_1d(mean)
        if not np.prod(bounds[:,0] < mean) & np.prod(mean < bounds[:,1]):
            raise ValueError("Mean is outside of the given bounds")
        mu_out = transform_to_probit(mean, bounds)
    elif samples is not None:
        mu_out = np.atleast_1d(np.mean(probit_samples, axis = 0))
    else:
        mu_out = np.zeros(dim)
    # L
    if cov is not None:
        L_out = cov
        draw_flag = True
    elif std is not None:
        L_out = np.identity(dim)*std**2
        draw_flag = True
    elif samples is not None:
        # 1/3 (arbitrary) std of samples
        L_out = np.atleast_2d(np.cov(probit_samples.T))/9
        diag  = np.sqrt(np.diag(L_out))
        stds  = np.minimum(diag, 0.2)
        L_out = L_out*np.outer(stds, stds)/np.outer(diag, diag)
    else:
        L_out = np.identity(dim)*0.2**2
    # k
    if k is not None:
        k_out = k
    else:
        k_out = 1e-2
    
    if draw_flag:
        ss = scipy.stats.multivariate_normal(np.mean(bounds, axis = -1), L_out).rvs(10000)
        if dim == 1:
            ss = np.atleast_2d(ss).T
        # Keeping only samples within bounds
        ss = ss[np.where((np.prod(bounds[:,0] < ss, axis = 1) & np.prod(ss < bounds[:,1], axis = 1)))]
        probit_samples = transform_to_probit(ss, bounds)
        L_out = np.atleast_2d(np.cov(probit_samples.T))
        
    return (k_out, L_out, df_out, mu_out)

def rvs_median(draws, n_draws):
    idx = np.random.choice(np.arange(len(draws)), size = n_draws)
    ctr = Counter(idx)
    samples = np.empty(shape = (1, draws[0].dim))
    for i, n in zip(ctr.keys(), ctr.values()):
        samples = np.concatenate((samples, draws[i].rvs(n)))
    return samples[1:]

#-------------#
#   Options   #
#-------------#

def save_options(options, out_folder):
    """
    Saves options for the run (reproducibility)
    
    Arguments:
        :dict options: options
    """
    logfile = open(Path(out_folder, 'options_log.txt'), 'w')
    for key, val in zip(vars(options).keys(), vars(options).values()):
        logfile.write('{0}: {1}\n'.format(key,val))
    logfile.close()

#-------------#
#    Plots    #
#-------------#

class PPPlot(axes.Axes):
    """
    Construct a probability-probability (P-P) plot.

    Derived from https://lscsoft.docs.ligo.org/ligo.skymap/_modules/ligo/skymap/plot/pp.html#PPPlot
    This class avoids installing the whole ligo.skymap.plot package.
    """

    name = 'pp_plot'

    def __init__(self, *args, **kwargs):
        # Call parent constructor
        super().__init__(*args, **kwargs)

        # Square axes, limits from 0 to 1
        self.set_aspect(1.0)
        self.set_xlim(0.0, 1.0)
        self.set_ylim(0.0, 1.0)

    def add_diagonal(self, *args, **kwargs):
        """
        Add a diagonal line to the plot, running from (0, 0) to (1, 1).

        Other parameters
        ----------------
        kwargs :
            optional extra arguments to `matplotlib.axes.Axes.plot`
        """
        # Make copy of kwargs to pass to plot()
        kwargs = dict(kwargs)
        kwargs.setdefault('color', 'black')
        kwargs.setdefault('linestyle', 'dashed')
        kwargs.setdefault('linewidth', 0.5)

        # Plot diagonal line
        return self.plot([0, 1], [0, 1], *args, **kwargs)

    def add_confidence_band(self, nsamples, cl=0.9, **kwargs):
        """
        Add a target confidence band.

        Parameters
        ----------
        nsamples : int
            Number of P-values
        cl : float, default: 0.9
            Confidence level

        Other parameters
        ----------------
        **kwargs :
            optional extra arguments to `matplotlib.axes.Axes.fill_betweenx`
        """
        n = nsamples
        k = np.arange(0, n + 1)
        p = k / n
        ci_lo, ci_hi = scipy.stats.beta.interval(cl, k + 1, n - k + 1)

        # Make copy of kwargs to pass to fill_betweenx()
        kwargs = dict(kwargs)
        kwargs.setdefault('color', 'ghostwhite')
        kwargs.setdefault('edgecolor', 'gray')
        kwargs.setdefault('linewidth', 0.5)
        kwargs.setdefault('alpha', 0.5)
        fontsize = kwargs.pop('fontsize', 'x-small')

        return self.fill_betweenx(p, ci_lo, ci_hi, **kwargs, label = '${0}\%\ CR$'.format(int(cl*100)))

    @classmethod
    def _as_mpl_axes(cls):
        """
        Support placement in figure using the `projection` keyword argument.
        See http://matplotlib.org/devel/add_new_projection.html.
        """
        return cls, {}
        
projection_registry.register(PPPlot)

def plot_median_cr(draws, injected = None, samples = None, selfunc = None, bounds = None, out_folder = '.', name = 'density', n_pts = 1000, label = None, unit = None, hierarchical = False, show = False, save = True, subfolder = False, true_value = None):
    """
    Plot the recovered 1D distribution along with the injected distribution and samples from the true distribution (both if available).
    
    Arguments:
        :iterable draws:                  container for mixture instances
        :callable or np.ndarray injected: injected distribution (if available)
        :np.ndarray samples:              samples from the true distribution (if available)
        :iterable bounds:                 bounds for the recovered distribution. If None, bounds from mixture instances are used.
        :str or Path out_folder:          output folder
        :str name:                        name to be given to outputs
        :int n_pts:                       number of points for linspace
        :str label:                       LaTeX-style quantity label, for plotting purposes
        :str unit:                        LaTeX-style quantity unit, for plotting purposes
        :bool hierarchical:               hierarchical inference, for plotting purposes
        :bool save:                       whether to save the plot or not
        :bool show:                       whether to show the plot during the run or not
    """
    if hierarchical:
        rec_label = '\mathrm{(H)DPGMM}'
    else:
        rec_label = '\mathrm{DPGMM}'
    
    all_bounds = np.atleast_2d([d.bounds[0] for d in draws])
    x_min = np.max(all_bounds[:,0])
    x_max = np.min(all_bounds[:,1])
    
    if bounds is not None:
        if not bounds[0] >= x_min:
            warnings.warn("The provided lower bound is invalid for at least one draw. {0} will be used instead.".format(x_min))
        else:
            x_min = bounds[0]
        if not bounds[1] <= x_max:
            warnings.warn("The provided upper bound is invalid for at least one draw. {0} will be used instead.".format(x_max))
        else:
            x_max = bounds[1]
    
    x    = np.linspace(x_min, x_max, n_pts+2)[1:-1]
    dx   = x[1]-x[0]
    
    probs = np.array([d.pdf(x) for d in draws])
    
    percentiles = [50, 5, 16, 84, 95]
    p = {}
    for perc in percentiles:
        p[perc] = np.percentile(probs, perc, axis = 0)
    norm = p[50].sum()*dx
    for perc in percentiles:
        p[perc] = p[perc]/norm
    
    fig, ax = plt.subplots()
    
    # Samples (if available)
    if samples is not None:
        ax.hist(samples, bins = int(np.sqrt(len(samples))), histtype = 'step', density = True, label = '$\mathrm{Samples}$', log = True)
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
    else:
        ax.set_yscale('log')
    
    # CR
    ax.fill_between(x, p[95], p[5], color = 'mediumturquoise', alpha = 0.5)
    ax.fill_between(x, p[84], p[16], color = 'darkturquoise', alpha = 0.5)
    # Injection (if available)
    if injected is not None:
        if callable(injected):
            p_x = injected(x)
        else:
            p_x = injected
        ax.plot(x, p_x, lw = 0.5, color = 'red', label = '$\mathrm{Simulated}$')
        if selfunc is not None:
            if callable(selfunc):
                f_x = selfunc(x)
            else:
                f_x = selfunc
            filtered_p_x = p_x*f_x
            ax.plot(x, filtered_p_x/np.sum(filtered_p_x*dx), lw = 0.5, color = 'k', label = '$\mathrm{Selection\ effects}$')
        
    # Median
    if true_value is not None:
        ax.axvline(true_value, ls = '--', color = 'r', lw = 0.5, label = '$\mathrm{True\ value}$')
    ax.plot(x, p[50], lw = 0.7, color = 'steelblue', label = '${0}$'.format(rec_label))
    if label is None:
        label = 'x'
    if unit is None or unit == '':
        ax.set_xlabel('${0}$'.format(label))
    else:
        ax.set_xlabel('${0}\ [{1}]$'.format(label, unit))
    ax.set_ylabel('$p({0})$'.format(label))
    if samples is not None:
        ax.set_xlim(xlim)
    ax.set_ylim(bottom = 1e-5, top = np.max(p[95])*1.1)
    ax.grid(True,dashes=(1,3))
    ax.legend(loc = 0, frameon = False)
    if save:
        if subfolder:
            plot_folder = Path(out_folder, 'density')
            if not plot_folder.exists():
                try:
                    plot_folder.mkdir()
                except FileExistsError:
                    # Avoids issue with parallelisation
                    pass
            log_folder = Path(out_folder, 'log_density')
            if not log_folder.exists():
                try:
                    log_folder.mkdir()
                except FileExistsError:
                    pass
            txt_folder = Path(out_folder, 'txt')
            if not txt_folder.exists():
                try:
                    txt_folder.mkdir()
                except FileExistsError:
                    pass
        else:
            plot_folder = out_folder
            log_folder  = out_folder
            txt_folder  = out_folder
        fig.savefig(Path(log_folder, 'log_{0}.pdf'.format(name)), bbox_inches = 'tight')
        ax.set_yscale('linear')
        ax.autoscale(True)
        if samples is not None:
            ax.set_xlim(xlim)
        fig.savefig(Path(plot_folder, '{0}.pdf'.format(name)), bbox_inches = 'tight')
        np.savetxt(Path(txt_folder, 'prob_{0}.txt'.format(name)), np.array([x, p[50], p[5], p[16], p[84], p[95]]).T, header = 'x 50 5 16 84 95')
    if show:
        ax.set_yscale('linear')
        ax.autoscale(True)
        if samples is not None:
            ax.set_xlim(xlim)
        plt.show()
    plt.close()
    
    # If selection function is available, plot reweighted distribution
    if injected is not None and selfunc is not None:
        for perc in percentiles:
            p[perc] = np.percentile((probs/f_x).T, perc, axis = 1)
        norm = p[50].sum()*dx
        for perc in percentiles:
            p[perc] = p[perc]/norm
        
        fig, ax = plt.subplots()
        ax.set_yscale('log')
        # CR
        ax.fill_between(x, p[95], p[5], color = 'mediumturquoise', alpha = 0.5)
        ax.fill_between(x, p[84], p[16], color = 'darkturquoise', alpha = 0.5)
        # Injection
        ax.plot(x, p_x, lw = 0.5, color = 'red', label = '$\mathrm{Simulated}$')
        # Median
        ax.plot(x, p[50], lw = 0.7, color = 'steelblue', label = '${0}$'.format(rec_label))
        if label is None:
            label = 'x'
        if unit is None or unit == '':
            ax.set_xlabel('${0}$'.format(label))
        else:
            ax.set_xlabel('${0}\ [{1}]$'.format(label, unit))
        ax.set_ylabel('$p({0})$'.format(label))
        ax.autoscale(True)
        ax.set_ylim(bottom = 1e-5, top = np.max(p[95])*1.1)
        ax.grid(True,dashes=(1,3))
        ax.legend(loc = 0, frameon = False)
        if save:
            fig.savefig(Path(log_folder, 'log_inj_{0}.pdf'.format(name)), bbox_inches = 'tight')
            ax.set_yscale('linear')
            ax.autoscale(True)
            fig.savefig(Path(plot_folder, 'inj_{0}.pdf'.format(name)), bbox_inches = 'tight')
            np.savetxt(Path(txt_folder, 'prob_inj_{0}.txt'.format(name)), np.array([x, p[50], p[5], p[16], p[84], p[95]]).T, header = 'x 50 5 16 84 95')
        if show:
            ax.set_yscale('linear')
            ax.autoscale(True)
            plt.show()
        plt.close()
    

def plot_multidim(draws, samples = None, out_folder = '.', name = 'density', labels = None, units = None, hierarchical = False, show = False, save = True, subfolder = False, n_pts = 200, true_value = None, figsize = 7, levels = [0.5, 0.68, 0.9]):
    """
    Plot the recovered multidimensional distribution along with samples from the true distribution (if available) as corner plot.
    
    Arguments:
        :iterable draws:         container for mixture instances
        :int dim:                number of dimensions
        :np.ndarray samples:     samples from the true distribution (if available)
        :str or Path out_folder: output folder
        :str name:               name to be given to outputs
        :list-of-str labels:     LaTeX-style quantity label, for plotting purposes
        :list-of-str units:      LaTeX-style quantity unit, for plotting purposes
        :bool hierarchical:      hierarchical inference, for plotting purposes
        :bool save:              whether to save the plot or not
        :bool show:              whether to show the plot during the run or not
        :bool subfolder:         whether to save in a dedicated subfolder
        :int n_pts:              number of grid points (same for each dimension)
        :iterable true_value:    true value to plot
        :double figsize:         figure size (matplotlib)
        :iterable levels:        credible levels to plot
    """
    
    dim = draws[0].dim
    
    if hierarchical:
        rec_label = '\mathrm{(H)DPGMM}'
    else:
        rec_label = '\mathrm{DPGMM}'
    
    if labels is None:
        labels = ['$x_{0}$'.format(i+1) for i in range(dim)]
    else:
        labels = ['${0}$'.format(l) for l in labels]
    
    if units is not None:
        labels = [l[:-1]+'\ [{0}]$'.format(u) if not u == '' else l for l, u in zip(labels, units)]
    
    levels = np.atleast_1d(levels)

    all_bounds = np.atleast_2d([d.bounds for d in draws])
    x_min = np.min(all_bounds, axis = -1).max(axis = 0)
    x_max = np.max(all_bounds, axis = -1).min(axis = 0)
    
    bounds = np.array([x_min, x_max]).T
    K = dim
    factor = 2.0          # size of one side of one panel
    lbdim = 0.5 * factor  # size of left/bottom margin
    trdim = 0.2 * factor  # size of top/right margin
    whspace = 0.1         # w/hspace size
    plotdim = factor * dim + factor * (K - 1.0) * whspace
    dim_plt = lbdim + plotdim + trdim
    
    fig, axs = plt.subplots(K, K, figsize=(figsize, figsize))
    # Format the figure.
    lb = lbdim / dim_plt
    tr = (lbdim + plotdim) / dim_plt
    fig.subplots_adjust(left=lb, bottom=lb, right=tr, top=tr, wspace=whspace, hspace=whspace)
    
    # 1D plots (diagonal)
    for column in range(K):
        ax = axs[column, column]
        # Marginalise over all uninterested columns
        dims = list(np.arange(dim))
        dims.remove(column)
        marg_draws = marginalise(draws, dims)
        # Credible regions
        lim = bounds[column]
        x = np.linspace(lim[0], lim[1], n_pts+2)[1:-1]
        dx   = x[1]-x[0]
        
        probs = np.array([d.pdf(x) for d in marg_draws])
        
        percentiles = [50, 5, 16, 84, 95]
        p = {}
        for perc in percentiles:
            p[perc] = np.percentile(probs, perc, axis = 0)
        norm = p[50].sum()*dx
        for perc in percentiles:
            p[perc] = p[perc]/norm
        
        # Samples (if available)
        if samples is not None:
            ax.hist(samples[:,column], bins = int(np.sqrt(len(samples[:,column]))), histtype = 'step', density = True)
        # CR
        ax.fill_between(x, p[95], p[5], color = 'mediumturquoise', alpha = 0.5)
        ax.fill_between(x, p[84], p[16], color = 'darkturquoise', alpha = 0.5)
        if true_value is not None:
            if true_value[column] is not None:
                ax.axvline(true_value[column], c = 'orangered', lw = 0.5)
        ax.plot(x, p[50], lw = 0.7, color = 'steelblue')
        if column < K - 1:
            ax.set_xticks([])
            ax.set_yticks([])
        elif column == K - 1:
            ax.set_yticks([])
            if labels is not None:
                ax.set_xlabel(labels[-1])
            ticks = np.linspace(lim[0], lim[1], 5)
            ax.set_xticks(ticks)
            [l.set_rotation(45) for l in ax.get_xticklabels()]
        ax.set_xlim(lim[0], lim[1])
    
    # 2D plots (off-diagonal)
    for row in range(K):
        for column in range(K):
            ax = axs[row,column]
            ax.grid(visible=False)
            if column > row:
                ax.set_frame_on(False)
                ax.set_xticks([])
                ax.set_yticks([])
                continue
            elif column == row:
                continue
            
            # Marginalise
            dims = list(np.arange(dim))
            dims.remove(column)
            dims.remove(row)
            marg_draws = marginalise(draws, dims)
            
            # Credible regions
            lim = bounds[[row, column]]
            grid, dgrid = recursive_grid(lim[::-1], np.ones(2, dtype = int)*int(n_pts))
            
            x = np.linspace(lim[0,0], lim[0,1], n_pts+2)[1:-1]
            y = np.linspace(lim[1,0], lim[1,1], n_pts+2)[1:-1]
            
            dd = np.array([d.pdf(grid) for d in marg_draws])
            median = np.percentile(dd, 50, axis = 0)
            median = median/(median.sum()*np.prod(dgrid))
            median = median.reshape(n_pts, n_pts)
            
            X,Y = np.meshgrid(x,y)
            with np.errstate(divide = 'ignore'):
                logmedian = np.nan_to_num(np.log(median), nan = -np.inf, neginf = -np.inf)
            _,_,levs = ConfidenceArea(logmedian, x, y, adLevels=levels)
            ax.contourf(Y, X, np.exp(logmedian), cmap = 'Blues', levels = 100)
            if true_value is not None:
                if true_value[row] is not None:
                    ax.axhline(true_value[row], c = 'orangered', lw = 0.5)
                if true_value[column] is not None:
                    ax.axvline(true_value[column], c = 'orangered', lw = 0.5)
                if true_value[column] is not None and true_value[row] is not None:
                    ax.plot(true_value[column], true_value[row], color = 'orangered', marker = 's', ms = 3)
            c1 = ax.contour(Y, X, logmedian, np.sort(levs), colors='k', linewidths=0.3)
            if rcParams["text.usetex"] == True:
                ax.clabel(c1, fmt = {l:'{0:.0f}\\%'.format(100*s) for l,s in zip(c1.levels, np.sort(levels)[::-1])}, fontsize = 3)
            else:
                ax.clabel(c1, fmt = {l:'{0:.0f}\%'.format(100*s) for l,s in zip(c1.levels, np.sort(levels)[::-1])}, fontsize = 3)
            ax.set_xticks([])
            ax.set_yticks([])
            
            if column == 0:
                ax.set_ylabel(labels[row])
                ticks = np.linspace(lim[0,0], lim[0,1], 5)
                ax.set_yticks(ticks)
                [l.set_rotation(45) for l in ax.get_yticklabels()]
            if row == K - 1:
                ticks = np.linspace(lim[1,0], lim[1,1], 5)
                ax.set_xticks(ticks)
                [l.set_rotation(45) for l in ax.get_xticklabels()]
                ax.set_xlabel(labels[column])
                
            elif row < K - 1:
                ax.set_xticks([])
            elif column == 0:
                ax.set_ylabel(labels[row])
                
    if show:
        plt.show()
    if save:
        if not subfolder:
            fig.savefig(Path(out_folder, '{0}.pdf'.format(name)), bbox_inches = 'tight')
        else:
            if not Path(out_folder, 'density').exists():
                try:
                    Path(out_folder, 'density').mkdir()
                except FileExistsError:
                    pass
            fig.savefig(Path(out_folder, 'density', '{0}.pdf'.format(name)), bbox_inches = 'tight')
    plt.close()

def plot_n_clusters_alpha(n_cl, alpha, out_folder = '.', name = 'event', show = False, save = True):
    """
    Plot the number of clusters and the concentration parameter as functions of the number of samples.
    
    Arguments:
        :np.ndarray n_cl:        number of active clusters
        :np.ndarray alpha:       concentration parameter
        :str or Path out_folder: output folder
        :str name:               name to be given to outputs
        :bool save:              whether to save the plot or not
        :bool show:              whether to show the plot during the run or not
    """
    fig, ax = plt.subplots()
    ax1 = ax.twinx()
    ax.plot(np.arange(1, len(n_cl)+1), n_cl, ls = '--', marker = '', lw = 0.7, color = 'k')
    ax1.plot(np.arange(1, len(alpha)+1), alpha, ls = '--', marker = '', lw = 0.7, color = 'r')
    ax.set_xlabel('$t$')
    ax.set_ylabel('$N_{\mathrm{cl}}(t)$', color = 'k')
    ax1.set_ylabel('$\alpha(t)$', color = 'r')
    ax.grid(True,dashes=(1,3))
    if show:
        plt.show()
    if save:
        fig.savefig(Path(out_folder, '{0}_n_cl_alpha.pdf'.format(name)), bbox_inches = 'tight')
    plt.close()

def pp_plot_cdf(draws, injection, n_points = 1000, out_folder = '.', name = 'event', show = False, save = True):
    """
    Make pp-plot comparing draws cdfs and injection cdf
    
    Arguments:
        :iterable draws:         container of mixture instances
        :callable injection:     injected density
        :int n_points:           number of points for linspace
        :str or Path out_folder: output folder
        :str name:               name to be given to outputs
        :bool save:              whether to save the plot or not
        :bool show:              whether to show the plot during the run or not
    """
    all_bounds = np.atleast_2d([d.bounds[0] for d in draws])
    x_min = np.max(all_bounds[:,0])
    x_max = np.min(all_bounds[:,1])
    x = np.linspace(x_min, x_max, n_points+2)[1:-1]
    
    functions     = np.array([mix(x) for mix in draws])
    median        = np.percentile(functions, 50, axis = 0)
    cdf_draws     = np.array([fast_cumulative(d) for d in functions])
    cdf_median    = fast_cumulative(median)
    cdf_injection = fast_cumulative(injection(x))
    
    fig = plt.figure()
    ax  = fig.add_subplot(111, projection = 'pp_plot')
    ax.add_confidence_band(len(cdf_median), cl=0.9, color = 'ghostwhite')
    ax.add_diagonal()
    for cdf in cdf_draws:
        ax.plot(cdf_injection, cdf, lw = 0.5, alpha = 0.5, color = 'darkturquoise')
    ax.plot(cdf_injection, cdf, color = 'steelblue', lw = 0.7)
    ax.set_xlabel('$\mathrm{Injected}$')
    ax.set_ylabel('$\mathrm{FIGARO}$')
    ax.grid(True,dashes=(1,3))
    if show:
        plt.show()
    if save:
        fig.savefig(Path(out_folder, '{0}_ppplot.pdf'.format(name)), bbox_inches = 'tight')
    plt.close()

def pp_plot_levels(CR_levels, median_CR = None, out_folder = '.', name = 'MDC', show = False, save = True):
    """
    Make pp-plot comparing draws cdfs and injection cdf
    
    Arguments:
        :iterable CR:            2D array with credible levels for each event
        :iterable median_CR:     credible levels of medians
        :str or Path out_folder: output folder
        :str name:               name to be given to outputs
        :bool save:              whether to save the plot or not
        :bool show:              whether to show the plot during the run or not
    """
    if len(CR_levels.shape) > 1:
        CR_levels = CR_levels.T
    n_evs     = CR_levels.shape[-1]
    L         = np.linspace(0,1,n_evs)
    
    fig = plt.figure()
    ax  = fig.add_subplot(111, projection = 'pp_plot')
    ax.add_confidence_band(n_evs, zorder = n_evs)
    ax.add_diagonal(zorder = n_evs+1)
    if len(CR_levels.shape) > 1:
        sorted = []
        for cr in CR_levels:
            if median_CR is not None:
                lw = 0.3
                c  = 'lightsteelblue'
            else:
                lw = 0.6
                c  = 'steelblue'
            ax.plot(np.sort(cr), L, lw = lw, alpha = 0.5, color = c)
        if median_CR is not None:
            ax.plot(np.sort(median_CR), L, lw = 0.8, color = 'steelblue', label = '$\mathrm{Median}$', zorder = n_evs+2)
        # Add label for draws
        handles, labels = ax.get_legend_handles_labels()
        line = Line2D([0], [0], label='$\mathrm{Draws}$', lw = lw, color = c)
        handles.extend([line])
        ax.legend(handles=handles, loc = 0, frameon = False)
    else:
        ax.plot(np.sort(CR_levels), L, lw = 0.8, color = 'steelblue', zorder = n_evs+2)
    # Maquillage
    ax.set_xlabel('$P$')
    ax.set_ylabel('$\mathrm{Fraction\ of\ events\ within\ }CR_P$')
    ax.grid(True,dashes=(1,3))
    if show:
        plt.show()
    if save:
        fig.savefig(Path(out_folder, '{0}_ppplot.pdf'.format(name)), bbox_inches = 'tight')
    plt.close()

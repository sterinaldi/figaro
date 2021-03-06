import numpy as np
from numba import jit, njit, prange

LOG2PI = np.log(2*np.pi)

#-----------#
# Functions #
#-----------#

@njit
def inv_jit(M):
  return np.linalg.inv(M)

@njit
def logdet_jit(M):
    return np.log(np.linalg.det(M))

@jit
def logsumexp_jit(a, b):
    a_max = np.max(a)
    tmp = b * np.exp(a - a_max)
    return np.log(np.sum(tmp)) + a_max

@njit
def scalar_product(v, M, n):
    """
    Scalar product: v*M*v^T
    
    Arguments:
        :np.ndarray v: array
        :np.ndarray M: matrix
        :int n:        len(v)
    
    Returns:
        :double: v*M*v^T
    """
    res = 0.
    for i in prange(n):
        for j in prange(n):
            res = res + M[i,j]*v[i]*v[j]
    return res

@jit
def log_norm_1d(x, m, s):
    """
    1D Normal logpdf
    
    Arguments:
        :double x: value
        :double m: mean
        :double s: var
    
    Returns:
        Normal(m,s).logpdf(x)
    """
    return -(x-m)**2/(2*s) - 0.5*np.log(2*np.pi*s)

@jit
def log_norm(x, mu, cov):
    """
    Multivariate Normal logpdf
    
    Arguments:
        :np.ndarray x: value
        :np.ndarray m: mean vector
        :np.ndarray s: covariance matrix
    
    Returns:
        :double: MultivariateNormal(m,s).logpdf(x)
    """
    inv_cov  = inv_jit(cov)
    exponent = -0.5*scalar_product(x-mu, inv_cov, len(mu))
    lognorm  = 0.5*len(mu)*LOG2PI+0.5*logdet_jit(cov)
    return -lognorm+exponent

#------------#
# 1D methods #
#------------#

@jit
def eval_mix_1d(mu, sigma, means, covs):
    """
    Computes N(mu_k| mu, (sigma_k^2+sigma^2) for all the components of a mixture (for predictive likelihood, 1D).
    
    Arguments:
        :np.ndarray mu:    temptative mean of the parent mixture component
        :np.ndarray sigma: temptative variance of the parent mixture component
        :np.ndarray means: means of the event mixture components
        :np.ndarray vars:  variances of the event mixture components
    
    Returns:
        :np.ndarray: probability for each event mixture components
    """
    return np.array([log_norm_1d(means[i,0], mu, sigma+covs[i,0,0]) for i in prange(len(means))])

@jit
def evaluate_mixture_MC_draws_1d(mu, sigma, means, vars, w):
    """
    Computes N(mu_k| mu, (sigma_k^2+sigma^2) for a set of MC draws for mu and sigma.
    
    Arguments:
        :np.ndarray mu:    MC draws for the mean of the parent mixture component
        :np.ndarray sigma: MC draws for the variance of the parent mixture component
        :np.ndarray means: means of the event mixture components
        :np.ndarray vars:  variances of the event mixture components
        :np.ndarray w:     component weights
    
    Returns:
        :np.ndarray: probability for each MC draw
    """
    logP = np.zeros(len(mu), dtype = np.float64)
    for i in prange(len(mu)):
        logP[i] = logsumexp_jit(eval_mix_1d(mu[i], sigma[i], means, vars), b = w)
    return logP

#------------#
# ND methods #
#------------#

@jit
def eval_mix(mu, sigma, means, covs):
    """
    Computes N(mu_k| mu, (sigma_k^2+sigma^2) for all the components of a mixture (for predictive likelihood, ND).
    
    Arguments:
        :np.ndarray mu:    temptative mean of the parent mixture component
        :np.ndarray sigma: temptative covariance matrix of the parent mixture component
        :np.ndarray means: means of the event mixture components
        :np.ndarray covs:  covariance matrices of the event mixture components
    
    Returns:
        :np.ndarray: probability for each event mixture components
    """
    return np.array([log_norm(means[i], mu, sigma+covs[i]) for i in prange(len(means))])

@jit
def evaluate_mixture_MC_draws(mu, sigma, means, covs, w):
    """
    Computes N(mu_k| mu, (sigma_k^2+sigma^2) for a set of MC draws for mu and sigma.
    
    Arguments:
        :np.ndarray mu:    MC draws for the mean vector of the parent mixture component
        :np.ndarray sigma: MC draws for the covariance matrix of the parent mixture component
        :np.ndarray means: means of the event mixture components
        :np.ndarray covs:  covariance matrices of the event mixture components
        :np.ndarray w:     component weights
    
    Returns:
        :np.ndarray: probability for each MC draw
    """
    logP = np.zeros(len(mu), dtype = np.float64)
    for i in prange(len(mu)):
        logP[i] = logsumexp_jit(eval_mix(mu[i], sigma[i], means, covs), b = w)
    return logP
